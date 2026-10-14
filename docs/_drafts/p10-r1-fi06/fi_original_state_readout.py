"""Structural projection of actual native decisions; unimported/unrun draft.

ROOT supplies only fixed-binding, owning, source-qualified native views/changes.
No context/geometry/score/revision decision is made here. Keep their actual owner
alive throughout projection; all numerical results must have come from CE.
"""
import pickle
import struct
import numpy as np

from .fi_original_state_codec import NONE,BOOL,INT,FLOAT,TEXT,BYTES,TUPLE,LIST,SET,FROZENSET,DICT

def read_tree(view):
    result={};visiting=set();pending=[(view.root,False)]
    while pending:
        index,finish=pending.pop()
        if index in result:continue
        if index>=view.nodes_n:raise ValueError('Native tree index exceeds actual owner')
        node=view.nodes[index]
        if (node.reserved or node.child_first>view.edges_n or node.child_count>view.edges_n-node.child_first
            or node.data_first>view.data_n or node.data_count>view.data_n-node.data_first):
            raise ValueError('Native tree field range invalid')
        kind=node.kind
        if kind in (TUPLE,LIST,SET,FROZENSET,DICT):
            children=tuple(view.edges[node.child_first+i] for i in range(node.child_count))
            if not finish:
                if index in visiting:raise ValueError('Cyclic native context projection is pendingport')
                visiting.add(index);pending.append((index,True))
                pending.extend((child,False) for child in reversed(children));continue
            values=tuple(result[child] for child in children);visiting.remove(index)
            result[index]={TUPLE:tuple,LIST:list,SET:set,FROZENSET:frozenset,DICT:dict}[kind](values)
        elif kind==NONE:result[index]=None
        elif kind==BOOL:
            if node.bits>1:raise ValueError('Invalid native boolean')
            result[index]=bool(node.bits)
        elif kind==FLOAT:result[index]=struct.unpack('=d',struct.pack('=Q',node.bits))[0]
        elif kind in (INT,TEXT,BYTES):
            raw=bytes(view.data[node.data_first+i] for i in range(node.data_count))
            result[index]=(int.from_bytes(raw,'little',signed=True) if kind==INT
                else raw.decode('utf-8','surrogatepass') if kind==TEXT else raw)
        else:raise ValueError('Unknown native context tag')
    return result[view.root]

def project_causal_map(original_payload,native_rows,source_names):
    """Called only with actual native owning change records after qualification.

    Rows expose source_token/universe, anchor_bytes (2048), context/motor_context,
    deps token tuple and have_attachment/attachment. All rows are NEW causal
    commits selected by native, not unmatched old rows or ordinary fade caches.
    Unchanged entries/unknown ordinary fields are restored from the complete old
    pickle. An existing key retains insertion position, like original dict update.
    """
    state=pickle.loads(original_payload)
    if type(state) is not dict:raise TypeError('Original retained causal payload is not dict')
    for row in native_rows:
        raw=row.anchor_bytes
        if type(raw) is not bytes or len(raw)!=2048:raise ValueError('Native anchor must own full512 float32 bytes')
        key=(source_names[row.source_token],row.universe)
        entry=dict(context=read_tree(row.context),motor_context=read_tree(row.motor_context),
            anchor=np.frombuffer(raw,dtype=np.float32,count=512).copy(),
            deps=frozenset(source_names[token] for token in row.deps))
        if row.have_attachment:entry['source_geometry']=read_tree(row.attachment)
        state[key]=entry
    return state
