"""Unimported original causal-state IO draft; no signature or math helper call.

Only ROOT-qualified ndarray/builtin/ctypes/pickle suppliers may use this seam.
The complete old dict is retained, including optional/unknown ordinary fields.
Numeric comparison, context normalization and source geometry decisions are C.
"""
import ctypes as ct
import pickle
import struct

import numpy as np

NONE,BOOL,INT,FLOAT,TEXT,BYTES,TUPLE,LIST,SET,FROZENSET,DICT=range(11)
NP_SCALARS=(np.bool_,np.int8,np.int16,np.int32,np.int64,np.uint8,np.uint16,np.uint32,np.uint64,np.float16,np.float32,np.float64)

class Node(ct.Structure):
    _fields_=[('kind',ct.c_uint32),('reserved',ct.c_uint32)]+[(name,ct.c_uint64) for name in
        ('object_token','bits','child_first','child_count','data_first','data_count')]

class View(ct.Structure):
    _fields_=[(name,ct.c_uint64) for name in ('root','identity_space','nodes_n','edges_n','data_n')]+[
        ('nodes',ct.POINTER(Node)),('edges',ct.POINTER(ct.c_uint64)),('data',ct.POINTER(ct.c_uint8))]

class LegacyRow(ct.Structure):
    _fields_=[('source_token',ct.c_uint64),('universe',ct.c_uint32),('reserved',ct.c_uint32)]+[
        (name,View) for name in ('context','motor_context','proof_schema','proof_slot','proof_values')]+[
        ('have_proof',ct.c_uint32),('geometry_reserved',ct.c_uint32),('deps_n',ct.c_uint64),
        ('deps',ct.POINTER(ct.c_uint64)),('anchor',ct.POINTER(ct.c_float))]

class Forest:
    def __init__(self):
        self.rows=[];self.edges=[];self.data=bytearray();self.memo={};self.refs=[]
        self.nodes=self.edge_owner=self.data_owner=None

    def add(self,value):
        # Iterative IO walk retains shared identities and cannot recurse through
        # an arbitrary graph on the host. Cyclic equality is handled as pending C.
        pending=[value];root=None
        while pending:
            item=pending.pop();token=id(item)
            if token in self.memo:
                continue
            kind=type(item);node=Node(object_token=token);index=len(self.rows)
            self.memo[token]=index;self.rows.append(node);self.refs.append(item)
            if root is None:root=index
            if kind is type(None):node.kind=NONE
            elif kind is bool:node.kind=BOOL;node.bits=item
            elif kind is int:
                node.kind=INT;size=(int.bit_length(item)+8)//8
                raw=int.to_bytes(item,size,'little',signed=True)
                node.data_first=len(self.data);node.data_count=len(raw);self.data.extend(raw)
            elif kind is float:
                node.kind=FLOAT;node.bits=struct.unpack('=Q',struct.pack('=d',item))[0]
            elif kind in (str,bytes):
                node.kind=TEXT if kind is str else BYTES
                raw=item.encode('utf-8','surrogatepass') if kind is str else item
                node.data_first=len(self.data);node.data_count=len(raw);self.data.extend(raw)
            elif kind in (tuple,list,set,frozenset,dict):
                node.kind={tuple:TUPLE,list:LIST,set:SET,frozenset:FROZENSET,dict:DICT}[kind]
                children=tuple(item.items()) if kind is dict else tuple(item)
                node.child_first=len(self.edges);node.child_count=len(children)
                self.edges.extend(children);pending.extend(reversed(children))
            else:
                raise TypeError('Foreign/nonbuiltin context scalar or container is pendingport')
        return self.memo[id(value)]

    def seal(self):
        self.nodes=(Node*len(self.rows))(*self.rows)
        self.edge_owner=(ct.c_uint64*len(self.edges))(*(self.memo[id(item)] for item in self.edges))
        self.data_owner=(ct.c_uint8*len(self.data)).from_buffer_copy(self.data)

    def view(self,root):
        return View(root=root,identity_space=1,nodes_n=len(self.rows),edges_n=len(self.edges),data_n=len(self.data),
                    nodes=self.nodes,edges=self.edge_owner,data=self.data_owner)

def ordinary_payload(value):
    pending=[value];seen=set()
    while pending:
        item=pending.pop();kind=type(item)
        if id(item) in seen:continue
        seen.add(id(item))
        if kind in (bool,int,float,str,bytes,type(None)) or kind in NP_SCALARS:
            continue
        if kind is np.ndarray:
            if item.dtype.hasobject:raise TypeError('Object payload array is pendingport')
        elif kind is dict:
            pending.extend(item.keys());pending.extend(item.values())
        elif kind in (tuple,list,set,frozenset):pending.extend(item)
        else:raise TypeError('Foreign causal payload is pendingport')

class PackedOriginal:
    def __init__(self,state,revision,source_tokens):
        if type(state) is not dict or type(revision) is not int or revision<0 or revision.bit_length()>64:
            raise TypeError('Exact original causal dict/revision required')
        ordinary_payload(state)
        self.original=state;self.revision=revision;self.forest=Forest();self.owners=[];spec=[]
        for key,entry in state.items():
            if (type(key) is not tuple or len(key)!=2 or type(key[0]) is not str or type(key[1]) is not int
                or key[1]<1 or key[1]>=64000 or type(entry) is not dict):
                raise TypeError('Original causal key/entry representation is pendingport')
            anchor=entry['anchor'];deps=entry['deps']
            if (type(anchor) is not np.ndarray or anchor.dtype!=np.dtype('float32') or anchor.shape!=(512,)
                or type(deps) is not frozenset or any(type(item) is not str for item in deps)):
                raise TypeError('Original anchor/dependency representation is pendingport')
            context=self.forest.add(entry['context']);motor=self.forest.add(entry['motor_context'])
            proof=entry.get('source_geometry');have=proof is not None
            if have:
                if type(proof) is not dict:raise TypeError('Foreign original source_geometry proof')
                schema=self.forest.add(proof.get('schema'));slot=self.forest.add(proof.get('slot'))
                values=self.forest.add((proof['token'],proof['interval'],proof['runtime']))
            else:schema=slot=values=self.forest.add(None)
            array=(ct.c_float*512).from_buffer_copy(anchor.tobytes(order='C'))
            tokens=(ct.c_uint64*len(deps))(*(source_tokens[item] for item in deps))
            self.owners.extend((array,tokens))
            spec.append((source_tokens[key[0]],key[1],context,motor,schema,slot,values,have,array,tokens))
        self.forest.seal()
        rows=[]
        for token,universe,context,motor,schema,slot,values,have,array,tokens in spec:
            rows.append(LegacyRow(source_token=token,universe=universe,
                context=self.forest.view(context),motor_context=self.forest.view(motor),
                proof_schema=self.forest.view(schema),proof_slot=self.forest.view(slot),proof_values=self.forest.view(values),
                have_proof=have,deps_n=len(tokens),deps=tokens,anchor=array))
        self.rows=(LegacyRow*len(rows))(*rows)
        # Existing structural pickle IO, never a signature/eligibility oracle.
        raw=pickle.dumps(state,protocol=5);self.payload=(ct.c_uint8*len(raw)).from_buffer_copy(raw)
        self.owners.extend((self.rows,self.payload))

    def retained_payload(self):
        return bytes(self.payload)
