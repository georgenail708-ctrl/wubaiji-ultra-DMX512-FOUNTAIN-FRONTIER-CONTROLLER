"""Declarations only. No native loader/dispatch/positions/count arithmetic.

R6 must use its genuine all-authored raw rows, current source and shared budget.
The C producer builds stride/offset metadata from actual offsetof declarations.
"""
import ctypes as ct


class Atom(ct.Structure):
    _fields_ = [('kind',ct.c_uint32),('reserved',ct.c_uint32),('bits',ct.c_uint64)]


class Request(ct.Structure):
    _fields_ = [('abi',ct.c_uint32),('size',ct.c_uint32),('rows',ct.c_void_p),
        ('count',ct.c_uint64),('row_bytes',ct.c_uint64),('stride',ct.c_uint32),
        ('start_offset',ct.c_uint32),('duration_offset',ct.c_uint32),
        ('timer_ms',ct.c_int32),('timer_present',ct.c_uint32),('reserved',ct.c_uint32)]


class Grid(ct.Structure):
    _fields_ = [('abi',ct.c_uint32),('size',ct.c_uint32),('status',ct.c_uint32),
        ('reserved',ct.c_uint32),('count',ct.c_uint64),('end',ct.c_double),('interval',ct.c_double)]


# Manual unobserved 64-bit layout: Atom16(0/4/8), Request56
# (abi0,size4,rows8,count16,row_bytes24,stride32,start36,duration40,timer44,
# present48,reserved52), Grid40(abi0,size4,status8,reserved12,count16,end24,dt32).
# Actual installed both-ISA sizes/field descriptors must independently qualify.
