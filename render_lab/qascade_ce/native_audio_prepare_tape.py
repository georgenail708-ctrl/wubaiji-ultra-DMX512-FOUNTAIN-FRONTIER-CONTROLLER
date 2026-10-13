"""Ordinary builtin conversion/status metadata; all decisions belong to C."""
import ctypes as ct

from .native_filter_prepare_metadata import number
from .native_audio_prepare_types import OP, Step, Request, Packed, array


class Tape:
    def __init__(self):
        self.steps, self.values, self.status, self.selectors, self.errors = [], [], [], [], {}

    def _error_index(self, error):
        index = len(self.errors)
        self.errors[index] = error
        return index

    def token(self, value):
        number_value, status, error = number(value, (TypeError, ValueError))
        index = len(self.values)
        self.values.append(number_value)
        self.status.append(status)
        self.selectors.append(-1 if error is None else self._error_index(error))
        return index

    def add(self, op, values, options):
        fallback, low, high, auxiliary = options
        inputs = [self.token(value) for value in values]
        inputs.extend([-1] * (3 - len(inputs)))
        index = len(self.steps)
        self.steps.append(Step(op=OP[op], input=(ct.c_int * 3)(*inputs), fallback=fallback,
                               low=low, high=high, auxiliary=auxiliary, first=-1, last=-1, skip_to=-1))
        return index

    def error(self, error):
        selector = self._error_index(error)
        return self.add('ERROR', (), (0., 0., 0., selector))

    def metadata(self, function, arguments):
        try:
            return function(*arguments)
        except Exception as error:
            self.error(error)
            return None

    def group(self, op, first):
        last = len(self.steps)
        index = self.add(op, (), (0., 0., 0., 0))
        self.steps[index].first, self.steps[index].last = first, last
        return first, index

    def pair(self, first, last):
        index = self.add('PAIR', (), (0., 0., 0., 0))
        self.steps[index].first, self.steps[index].last = first, last
        return index

    def pack(self):
        size = len(self.steps)
        buffers = dict(step=array(Step, self.steps), value=array(ct.c_double, self.values),
                       status=array(ct.c_int, self.status), errors=array(ct.c_int, self.selectors),
                       output=array(ct.c_double, (0.,) * (4 * size)), flags=array(ct.c_int, (0,) * size),
                       order=array(ct.c_int, (0,) * size), count=array(ct.c_int, (0,) * size))
        request = Request(steps=size, values=len(self.values), **buffers)
        return Packed(request, buffers, self.errors)
