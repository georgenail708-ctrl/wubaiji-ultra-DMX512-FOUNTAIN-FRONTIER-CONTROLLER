"""Actual committed native DLL and ctypes ABI, not product admission or a bake."""
import argparse
import ctypes as ct
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dll',type=Path,required=True)
    parser.add_argument('--types',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('reviewed_g0_types',args.types.resolve(strict=True))
    types = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(types)
    dll = ct.CDLL(str(args.dll.resolve(strict=True)))
    size = dll.ce_main_grid_size_g0
    size.argtypes,size.restype = [ct.c_uint32],ct.c_uint32
    state_size = dll.ce_main_grid_state_size_g0_v7
    state_size.argtypes,state_size.restype = [],ct.c_uint32
    begin = dll.ce_main_grid_begin_g0_v7
    begin.argtypes,begin.restype = [ct.POINTER(types.Request),ct.POINTER(types.CompileState)],ct.c_int32
    step = dll.ce_main_grid_step_g0_v7
    step.argtypes,step.restype = [ct.POINTER(types.CompileState),ct.c_uint32,ct.POINTER(types.Grid)],ct.c_int32
    expected = ((types.Atom,16),(types.Request,56),(types.Grid,40),(types.CompileState,96))
    for index,(kind,wanted) in enumerate(expected):
        assert ct.sizeof(kind)==wanted
        assert (size(index) if index<3 else state_size())==wanted
    request = types.Request()
    request.abi,request.size,request.stride = 6,ct.sizeof(request),16
    state,grid = types.CompileState(),types.Grid()
    assert begin(ct.byref(request),ct.byref(state))==7
    ct.memset(ct.byref(grid),0x5a,ct.sizeof(grid))
    original = bytes(grid)
    assert step(ct.byref(state),0,ct.byref(grid))==7 and bytes(grid)==original
    assert step(ct.byref(state),1,ct.byref(grid))==0
    assert state.completed_units==1 and grid.count==1 and grid.end==0. and grid.interval==.023
    receipt = {'status':'ACTUAL_G0_ABI_ONLY','dll':str(args.dll),
        'sizes':{kind.__name__:ct.sizeof(kind) for kind,_ in expected},
        'offsets':{kind.__name__:{name:getattr(kind,name).offset for name,*_ in kind._fields_} for kind,_ in expected}}
    args.out.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
