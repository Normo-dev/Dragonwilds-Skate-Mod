"""Appearance adapter for poses from the existing Skate 3 mashup.

Adapted from chasmlol/2010-rust-rewrite-mashup render_anim/src/skate/rig.rs
(Apache-2.0). This module renders poses; it contains no skating simulation.
"""
import json
import numpy as np
from runtime_paths import load_paths

BASIS = np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]],dtype=np.float64)
MAPPING = {
    'Pelvis':('HIPS','spine_01','SPINE'),
    'spine_01':('SPINE','spine_02','SPINE1'),
    'spine_02':('SPINE1','spine_03','SPINE3'),
    'spine_03':('SPINE3','neck_01','NECK'),
    # Like the existing mashup, let the host neck/head inherit the chest's
    # skinning delta. Applying retail head axes directly turned this rig back.
    'clavicle_l':('LEFTSHOULDER','UpperArm_L','LEFTARM'),
    'UpperArm_L':('LEFTARM','lowerarm_l','LEFTFOREARM'),
    'lowerarm_l':('LEFTFOREARM','Hand_L','LEFTHAND'), 'Hand_L':('LEFTHAND',None,None),
    'clavicle_r':('RIGHTSHOULDER','UpperArm_R','RIGHTARM'),
    'UpperArm_R':('RIGHTARM','lowerarm_r','RIGHTFOREARM'),
    'lowerarm_r':('RIGHTFOREARM','Hand_R','RIGHTHAND'), 'Hand_R':('RIGHTHAND',None,None),
    'Thigh_L':('LEFTUPLEG','calf_l','LEFTLEG'), 'calf_l':('LEFTLEG','foot_l','LEFTFOOT'),
    'foot_l':('LEFTFOOT','ball_l','LEFTTOEBASE'), 'ball_l':('LEFTTOEBASE',None,None),
    'Thigh_R':('RIGHTUPLEG','calf_r','RIGHTLEG'), 'calf_r':('RIGHTLEG','foot_r','RIGHTFOOT'),
    'foot_r':('RIGHTFOOT','ball_r','RIGHTTOEBASE'), 'ball_r':('RIGHTTOEBASE',None,None),
}
# The host's visible front is opposite the donor bind frame. A 180-degree
# facing fit also changes which side of the donor occupies each host limb.
# Keeping same-named sides forced the arm fits through 180-degree rolls and
# put each thigh above the other donor foot. Match sides AFTER calibration.
def opposite_side(name):
    if name is None: return None
    return name.replace('LEFT','_RIGHT_').replace('RIGHT','LEFT').replace('_LEFT_','RIGHT')
MAPPING={host:(opposite_side(source),child,opposite_side(source_child))
         for host,(source,child,source_child) in MAPPING.items()}

def rotation(q):
    x,y,z,w=np.asarray(q,dtype=float)/np.linalg.norm(q)
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])

def matrix(t):
    q=t['Rotation'];p=t['Translation'];s=t['Scale3D']
    out=np.eye(4)
    out[:3,:3]=rotation([q[k] for k in 'XYZW'])@np.diag([s[k] for k in 'XYZ'])
    out[:3,3]=[p[k] for k in 'XYZ']
    return out

def quat(r):
    trace=np.trace(r)
    if trace>0:
        s=np.sqrt(trace+1)*2
        return [(r[2,1]-r[1,2])/s,(r[0,2]-r[2,0])/s,(r[1,0]-r[0,1])/s,s/4]
    i=int(np.argmax(np.diag(r)));j=(i+1)%3;k=(i+2)%3
    s=np.sqrt(max(0,1+r[i,i]-r[j,j]-r[k,k]))*2
    q=np.zeros(4);q[i]=s/4;q[j]=(r[j,i]+r[i,j])/s;q[k]=(r[k,i]+r[i,k])/s;q[3]=(r[k,j]-r[j,k])/s
    return q.tolist()

def transform(m):
    scale=np.linalg.norm(m[:3,:3],axis=0)
    r=m[:3,:3]/np.maximum(scale,1e-12)
    q=np.asarray(quat(r));q/=np.linalg.norm(q)
    return {'Rotation':dict(zip('XYZW',np.round(q,7).tolist())),
            'Translation':dict(zip('XYZ',np.round(m[:3,3],4).tolist())),
            'Scale3D':dict(zip('XYZ',np.round(scale,7).tolist()))}

def transforms(mats):
    """Batch the small matrix operations rather than 84 NumPy calls per bone."""
    mats=np.asarray(mats)
    scale=np.linalg.norm(mats[:,:3,:3],axis=1)
    r=mats[:,:3,:3]/np.maximum(scale[:,None,:],1e-12)
    trace=np.trace(r,axis1=1,axis2=2)
    q=np.zeros((len(mats),4))
    mask=trace>0
    s=np.sqrt(trace[mask]+1)*2
    a=r[mask]
    q[mask]=np.column_stack(((a[:,2,1]-a[:,1,2])/s,(a[:,0,2]-a[:,2,0])/s,(a[:,1,0]-a[:,0,1])/s,s/4))
    greatest=np.argmax(np.diagonal(r,axis1=1,axis2=2),axis=1)
    for i in range(3):
        mask=(trace<=0)&(greatest==i)
        if not np.any(mask): continue
        j=(i+1)%3;k=(i+2)%3;a=r[mask]
        s=np.sqrt(np.maximum(0,1+a[:,i,i]-a[:,j,j]-a[:,k,k]))*2
        values=np.zeros((len(a),4));values[:,i]=s/4
        values[:,j]=(a[:,j,i]+a[:,i,j])/s;values[:,k]=(a[:,k,i]+a[:,i,k])/s;values[:,3]=(a[:,k,j]-a[:,j,k])/s
        q[mask]=values
    q/=np.linalg.norm(q,axis=1)[:,None]
    qs=np.round(q,7).tolist();ps=np.round(mats[:,:3,3],4).tolist();ss=np.round(scale,7).tolist()
    return [{'Rotation':dict(zip('XYZW',v)), 'Translation':dict(zip('XYZ',p)),
             'Scale3D':dict(zip('XYZ',s))} for v,p,s in zip(qs,ps,ss)]

def converted(m):
    out=BASIS@m@BASIS.T
    out[:3,3]*=100
    return out

def arc(a,b):
    a=a/np.linalg.norm(a);b=b/np.linalg.norm(b)
    dot=np.clip(np.dot(a,b),-1,1)
    if dot<-.999999:
        axis=np.cross(a,[1,0,0] if abs(a[0])<.8 else [0,1,0]);axis/=np.linalg.norm(axis)
        return rotation([*axis,0])
    q=np.r_[np.cross(a,b),1+dot]
    return rotation(q)

def rigid_rotation(m):
    """Remove animation scale/shear before transferring a joint orientation."""
    u,_,v=np.linalg.svd(m[:3,:3])
    r=u@v
    if np.linalg.det(r)<0:
        u[:,-1]*=-1
        r=u@v
    return r

class HostRender:
    def __init__(self,mesh,rig_path,publish):
        self.mesh=mesh
        self.rig=json.loads(rig_path.read_text(encoding='utf-8'))['bones']
        self.bind={b['name']:matrix(b['bind']) for b in self.rig}
        self.parent={b['name']:b['parent'] for b in self.rig}
        self.local={n:np.linalg.inv(self.bind[p])@m if p in self.bind else m.copy()
                    for n,m in self.bind.items() for p in [self.parent[n]]}
        self.source={name:converted(np.linalg.inv(mesh.rb@mesh.inverse[i])) for i,name in enumerate(mesh.names)}
        self.fits={}
        alignments={}
        # Dragonwilds' authored character faces the opposite direction to the
        # reference skater. Apply the facing calibration before segment fits,
        # as the original mashup does for its host skeleton.
        facing=np.diag([-1.,-1.,1.])
        self.facing=facing
        for bone in self.rig:
            name=bone['name'];mapping=MAPPING.get(name)
            if mapping is None: continue
            source,child,source_child=mapping
            a=self.bind[name][:3,3];b=self.source[source][:3,3]
            if child is not None:
                align=arc(facing@(self.bind[child][:3,3]-a),self.source[source_child][:3,3]-b)@facing
            else:
                align=alignments.get(bone['parent'],facing)
            alignments[name]=align
            self.fits[name]=rigid_rotation(self.source[source]).T@align@rigid_rotation(self.bind[name])
        self.board_names=[]
        self.native_renderer=None
        topology=[]
        deck_texture=load_paths().get('board_deck_texture')
        if deck_texture is not None and not deck_texture.is_file():
            raise ValueError('Configured skateboard deck texture is missing')
        for section_index in [7,8,9]:
            positions,normals,joints,weights=mesh.surfaces[section_index]
            section=mesh.topology[section_index]
            dominant=joints[np.arange(len(joints)),weights.argmax(axis=1)]
            if np.any(np.max(weights,axis=1)<.999): raise ValueError('Board mesh is not rigidly skinned')
            triangles=np.asarray(section['triangles']).reshape(-1,3)
            triangle_joints=dominant[triangles]
            mixed=np.any(triangle_joints!=triangle_joints[:,0,None],axis=1)
            # A few truck seams join two rigid bodies. Keep their authored
            # geometry and attach each triangle to its majority bone. The
            # source simulation still solves all board/truck/wheel bodies.
            owner=triangle_joints[:,0].copy()
            same_last=triangle_joints[:,1]==triangle_joints[:,2]
            owner[same_last]=triangle_joints[same_last,1]
            if np.any(mixed): print('Rigid board seam triangles:',int(mixed.sum()),flush=True)
            for joint in np.unique(dominant):
                selected=triangles[owner==joint]
                used=np.unique(selected);lookup=np.full(len(positions),-1);lookup[used]=np.arange(len(used))
                local=mesh.rb@mesh.inverse[joint]
                p=(local@positions[used].T).T[:,:3]
                n=(local[:3,:3]@normals[used].T).T
                p=(BASIS[:3,:3]@p.T).T*100;n=(BASIS[:3,:3]@n.T).T
                self.board_names.append(mesh.names[joint])
                themed=deck_texture is not None
                tint=({7:[1.,1.,1.,1.],8:[.32,.30,.27,1.],9:[.96,.87,.69,1.]}[section_index]
                      if themed else [1.,1.,1.,1.])
                texture=deck_texture.as_posix() if themed and section_index==7 else mesh.texture_paths[section_index]
                topology.append({'bone':mesh.names[joint], 'texture':texture, 'tint':tint, 'vertices':np.round(p,4).tolist(),
                    'normals':np.round(n,6).tolist(),'triangles':lookup[selected].reshape(-1).tolist(),
                    'uv':[section['uv'][i] for i in used], 'colors':[[1.,1.,1.,1.] if themed else section['colors'][i] for i in used]})
        # The native hierarchy leaves every other bone at parent * bind_local.
        # This optional contract lets the host avoid redundant component-space
        # setters. The host validates its exact skeleton and checks the first
        # sparse pose by native readback, falling back to full updates on doubt.
        pose_layout={'schema':'S3HOSTPOSE1','bones':[
            {'name':b['name'],'parent':b['parent'],
             'driven':b['name']=='Root' or b['name'] in self.fits}
            for b in self.rig]}
        publish('board-topology.json',{'parts':topology,'host_pose_layout':pose_layout})
        print('Host renderer ready:',len(self.rig),'bones,',len(topology),'rigid board parts',flush=True)

    def body_matrices(self,posed):
        # Transfer absolute joint orientations, then rebuild the hierarchy with
        # the HOST's local offsets. Copying donor joint positions shortened one
        # spine segment by 9 cm and lengthened the next by 11 cm, tearing clothes.
        desired={n:rigid_rotation(posed[MAPPING[n][0]])@fit for n,fit in self.fits.items()}
        def hierarchy():
            out={}
            for bone in self.rig:
                n,p=bone['name'],bone['parent']
                parent=out.get(p)
                m=(parent@self.local[n]) if parent is not None else self.local[n].copy()
                if n=='Root': m[:3,:3]=self.facing@rigid_rotation(self.bind[n])
                if n in desired:
                    m[:3,:3]=desired[n]@np.diag(np.linalg.norm(self.bind[n][:3,:3],axis=0))
                if n=='Pelvis': m[:3,3]=posed['HIPS'][:3,3]
                out[n]=m
            return out
        out=hierarchy()
        # Preserve foot contact while keeping the host thigh/calf lengths.
        # This only fits the visible skeleton to the source's solved feet; the
        # original Skate 3 worker continues to own physics and footplant state.
        for upper,lower,end,source_lower,source_end in [
            ('Thigh_L','calf_l','foot_l','RIGHTLEG','RIGHTFOOT'),
            ('Thigh_R','calf_r','foot_r','LEFTLEG','LEFTFOOT')]:
            a=out[upper][:3,3]; target=posed[source_end][:3,3]
            vector=target-a; distance=np.linalg.norm(vector)
            if distance<1e-7: continue
            direction=vector/distance
            l1=np.linalg.norm(self.local[lower][:3,3]);l2=np.linalg.norm(self.local[end][:3,3])
            d=np.clip(distance,abs(l1-l2)+1e-5,l1+l2-1e-5)
            pole=posed[source_lower][:3,3]-a
            pole-=direction*np.dot(pole,direction)
            if np.linalg.norm(pole)<1e-6:
                pole=out[lower][:3,3]-a
                pole-=direction*np.dot(pole,direction)
            if np.linalg.norm(pole)<1e-6:
                axis=np.eye(3)[int(np.argmin(np.abs(direction)))]
                pole=axis-direction*np.dot(axis,direction)
            pole/=np.linalg.norm(pole)
            along=(l1*l1-l2*l2+d*d)/(2*d)
            knee=a+direction*along+pole*np.sqrt(max(0,l1*l1-along*along))
            desired[upper]=arc(out[lower][:3,3]-a,knee-a)@rigid_rotation(out[upper])
            desired[lower]=arc(out[end][:3,3]-out[lower][:3,3],a+direction*d-knee)@rigid_rotation(out[lower])
        out=hierarchy()
        return [out[b['name']] for b in self.rig]

    def pose(self,response):
        native=np.asarray(response['bones'],dtype=float).reshape(-1,4,4).transpose(0,2,1)
        root=np.asarray(response['root'],dtype=float).reshape(4,4).T
        native_by_name=dict(zip(response['names'],native))
        posed={name:converted(bone) for name,bone in native_by_name.items()}
        matrices=self.body_matrices(posed)
        if not np.isfinite(matrices).all(): raise ValueError('Nonfinite host pose')
        matrices.extend(posed[name] for name in self.board_names)
        matrices.append(converted(root))
        packed=transforms(matrices)
        bones=[{'name':bone['name'],'transform':t} for bone,t in zip(self.rig,packed)]
        board=packed[len(self.rig):-1]
        return bones,board,packed[-1]

    def pose_flat(self,response):
        if self.native_renderer is None:
            from skate_native_render import NativeRender
            self.native_renderer=NativeRender(self,response['names'])
            print('Compiled appearance adapter ready',flush=True)
        return self.native_renderer.pose(response)
