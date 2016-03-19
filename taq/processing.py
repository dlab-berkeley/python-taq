'''Tools for operating on chunks of financial time-series data.

'''

from string import ascii_uppercase
from random import sample

import numpy as np


def split_chunks(iterator_in, columns):
    '''Split a chunk based on a list of columns

    Note that if the next chunk exhibits the continuation of a symbol, this
    will NOT combine derived chunks for the same symbol.'''

    for chunk in iterator_in:
        unique_symbols, start_indices = \
            np.unique(chunk[columns], return_index=True)
        # This takes up a trivial amount of memory, due to the use of views
        # And of course we don't want to split on the first index, it's 0
        if len(start_indices) > 1:
            # If we have only one record and no splits, this would be an error
            for split_c in np.split(chunk, start_indices[1:]):
                yield split_c
        else:
            # The chunk is uniform
            yield chunk


def joined_chunks(iterator_in, columns, row_limit=np.inf):
    '''If a chunk matches the columns from a previous chunk, concatenate!

    The logic only inspects the first record. `row_limit` is provided to help
    ensure memory limits. But is NOT a limit on records in memory (you can have
    about the row_limit + size of the base chunks coming off disk)'''

    to_join = []
    total_len = 0

    for chunk in iterator_in:
        # Something is in to_join and (we could blow our row_limit or we have a
        # mis-match)
        if to_join and (total_len + len(chunk) > row_limit or
                        to_join[0][columns][0] != chunk[columns][0]):
            yield np.hstack(to_join)
            to_join = [chunk]
            total_len = len(chunk)
        # Nothing there yet or we have a match and space to join
        else:
            to_join.append(chunk)
            total_len += len(chunk)

    # Get our last chunk
    yield np.hstack(to_join)


def downsample(iterator_in, p=0.001):
    '''Return a random set of records for each chunk, with probability `p` for
    each record'''

    for chunk in iterator_in:
        recs = np.random.binomial(1, p, len(chunk)).astype(bool)
        # ensure we always have some content from each chunk
        # Could also skip returning if a recs is all false...
        recs[np.random.randint(len(recs))] = True
        yield chunk[recs]


class ProcessChunk:
    '''An abstract base class for processing chunks.

    A class-based structure is unnecessary in the straightforward generator
    functions above. But once we start having a bit more structure, this allows
    something with a bit more flexibility.

    Probably we should be using Dask or Blaze or something. Next step, maybe?
    '''

    def __init__(self, iterator_in, *args, **kwargs):
        '''Initialize a derived iterator.

        See the _process_chunks method for arguments.'''

        self.iterator = self._process_chunks(iterator_in, *args, **kwargs)

    def __iter__(self):
        # Returning the internal iterator avoids a function call, not a big
        # deal, but may as well avoid extra computation
        return self.iterator

    def __next__(self):
        return next(self.iterator)

    def _process_chunks(self, *args, **kwargs):
        raise NotImplementedError('Please subclass ProcessChunk')


class Sanitizer(ProcessChunk):
    '''Take a TAQ file and make it fake while preserving structure'''

    # These could be overriden as desired
    fudge_columns = ['Bid_Price', 'Bid_Size', 'Ask_Price', 'Ask_Size']

    # This will preserve the fake symbol across chunks
    symbol_map = {}
    ascii_bytes = ascii_uppercase.encode('ascii')

    def _process_chunks(self, iterator_in):
        '''Return chunks with changed symbols and fudged times and values.

        For now, successive calls will result in a dropped chunk.'''
        # last_symbol = None
        for chunk in iterator_in:
            # XXX a little annoying AND undocumented that split makes
            # thing unwriteable. Should double-check.
            chunk.flags.writeable = True
            self.fake_symbol_replace(chunk)
            self.fudge_up(chunk)

            yield chunk

    def fake_symbol_replace(self, chunk, symbol_column='Symbol_root'):
        '''Make a new fake symbol if we don't have it yet, and return it'''
        real_symbol = chunk[symbol_column][0]
        new_fake_symbol = bytes(sample(self.ascii_bytes, len(real_symbol)))
        fake_symbol = self.symbol_map.setdefault(real_symbol, new_fake_symbol)

        chunk[symbol_column] = fake_symbol

    def fudge_up(self, chunk):
        '''Increase each entry in column by some random increment.

        Make sure the values stay monotonic, and don't get bigger than
        max_value.'''

        for col in self.fudge_columns:
            # Note that we don't worry about decimal place here - just treating
            # everything as an integer is fine for this purpose
            data = chunk[col].astype(np.int64)
            mean_val = np.mean(data)
            std_val = np.std(data)
            fake_data = (np.random.standard_normal(len(data)) *
                         std_val + mean_val).astype(np.int64)
            # np.min wasn't working here
            fake_data[fake_data < 0] = 0

            num_bytes = len(chunk[0][col])
            fake_bytes = np.char.zfill(
                fake_data.astype('S{}'.format(num_bytes)), num_bytes)

            # this is where the side-effects happen
            chunk[col] = fake_bytes
