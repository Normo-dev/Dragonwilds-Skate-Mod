//! Character pose fitting only. Mirrors the verified Python adapter.
use glam::{DMat3,DMat4,DQuat,DVec3};
use std::sync::Mutex;
#[repr(C)] #[derive(Clone,Copy)] pub struct Bone { parent:i32,source:i32,local:[f64;16],fit:[f64;9],scale:[f64;3] }
struct Rig { bones:Vec<Bone>,legs:[i32;10],board:Vec<i32> }
static RIG:Mutex<Option<Rig>>=Mutex::new(None);
fn converted(m:DMat4)->DMat4 {
    let b=DMat4::from_cols_array(&[1.,0.,0.,0.,0.,0.,1.,0.,0.,-1.,0.,0.,0.,0.,0.,1.]);
    let mut m=b*m*b.transpose();m.w_axis.x*=100.;m.w_axis.y*=100.;m.w_axis.z*=100.;m
}
fn rot(m:DMat4)->DMat3 {
    let mut r=DMat3::from_mat4(m);
    for _ in 0..5 { r=(r+r.inverse().transpose())*0.5; }
    r
}
fn arc(a:DVec3,b:DVec3)->DMat3 {
    let a=a.normalize();let b=b.normalize();let dot=a.dot(b).clamp(-1.,1.);
    let q=if dot < -0.999999 {
        let axis=a.cross(if a.x.abs()<0.8 {DVec3::X}else{DVec3::Y}).normalize();
        DQuat::from_xyzw(axis.x,axis.y,axis.z,0.)
    } else { let cross=a.cross(b);DQuat::from_xyzw(cross.x,cross.y,cross.z,1.+dot).normalize() };
    DMat3::from_quat(q)
}
fn hierarchy(rig:&Rig,desired:&[Option<DMat3>],hips:DVec3)->Vec<DMat4> {
    let mut out:Vec<DMat4>=Vec::with_capacity(rig.bones.len());
    for (i,b) in rig.bones.iter().enumerate() {
        let local=DMat4::from_cols_array(&b.local);
        let mut m=if b.parent>=0 {out[b.parent as usize]*local}else{local};
        if i==0 { m=DMat4::from_rotation_z(std::f64::consts::PI)*local; }
        if let Some(r)=desired[i] {
            let r=r*DMat3::from_diagonal(DVec3::from_array(b.scale));
            m.x_axis=r.x_axis.extend(0.);m.y_axis=r.y_axis.extend(0.);m.z_axis=r.z_axis.extend(0.);
        }
        if i==1 { m.w_axis=hips.extend(1.); }
        out.push(m);
    }
    out
}
fn pack(m:DMat4,out:&mut [f64]) {
    let scale=DVec3::new(m.x_axis.truncate().length(),m.y_axis.truncate().length(),m.z_axis.truncate().length());
    let r=DMat3::from_cols(m.x_axis.truncate()/scale.x,m.y_axis.truncate()/scale.y,m.z_axis.truncate()/scale.z);
    let q=DQuat::from_mat3(&r).normalize();
    out[..3].copy_from_slice(&m.w_axis.truncate().to_array());
    out[3..7].copy_from_slice(&q.to_array());out[7..].copy_from_slice(&scale.to_array());
}
#[unsafe(no_mangle)] pub unsafe extern "C" fn rig_init(bones:*const Bone,n:usize,legs:*const i32,board:*const i32,nb:usize)->i32 {
    if bones.is_null() || legs.is_null() || board.is_null() || n<2 || n>256 || nb>32 {return 0;}
    let bones=unsafe{std::slice::from_raw_parts(bones,n)};
    if bones.iter().enumerate().any(|(i,b)|b.parent>=i as i32 || b.parent< -1 || b.source< -1 || b.source>256 || b.local.iter().chain(b.fit.iter()).chain(b.scale.iter()).any(|v|!v.is_finite())) {return 0;}
    let legs: [i32;10]=unsafe{std::slice::from_raw_parts(legs,10)}.try_into().unwrap();
    let board=unsafe{std::slice::from_raw_parts(board,nb)}.to_vec();
    *RIG.lock().unwrap()=Some(Rig{bones:bones.to_vec(),legs,board});1
}
#[unsafe(no_mangle)] pub unsafe extern "C" fn rig_pose(input:*const f64,ns:usize,root:*const f64,output:*mut f64,no:usize)->i32 {
    if input.is_null() || root.is_null() || output.is_null() || ns>256 {return 0;}
    let lock=RIG.lock().unwrap();let Some(rig)=lock.as_ref() else{return 0;};
    if no!=(rig.bones.len()+rig.board.len()+1)*10 {return 0;}
    let input=unsafe{std::slice::from_raw_parts(input,ns*16)};
    if input.iter().any(|v|!v.is_finite()) {return 0;}
    let source:Vec<_>=input.chunks_exact(16).map(|v|converted(DMat4::from_cols_array(v.try_into().unwrap()))).collect();
    if rig.bones.iter().any(|b|b.source>=ns as i32) || rig.board.iter().any(|b|*b<0 || *b>=ns as i32) {return 0;}
    let mut desired:Vec<_>=rig.bones.iter().map(|b|if b.source>=0 {Some(rot(source[b.source as usize])*DMat3::from_cols_array(&b.fit))}else{None}).collect();
    let hips=source[rig.bones[1].source as usize].w_axis.truncate();
    let mut out=hierarchy(rig,&desired,hips);
    for leg in rig.legs.chunks_exact(5) {
        if leg[..3].iter().any(|i|*i<0 || *i>=rig.bones.len() as i32) || leg[3..].iter().any(|i|*i<0 || *i>=ns as i32) {return 0;}
        let (upper,lower,end)=(leg[0] as usize,leg[1] as usize,leg[2] as usize);
        let a=out[upper].w_axis.truncate();let target=source[leg[4] as usize].w_axis.truncate();
        let vector=target-a;let distance=vector.length();if distance<1e-7 {continue;}
        let direction=vector/distance;
        let local1=DMat4::from_cols_array(&rig.bones[lower].local);let local2=DMat4::from_cols_array(&rig.bones[end].local);
        let l1=local1.w_axis.truncate().length();let l2=local2.w_axis.truncate().length();
        let d=distance.clamp((l1-l2).abs()+1e-5,l1+l2-1e-5);
        let mut pole=source[leg[3] as usize].w_axis.truncate()-a;pole-=direction*pole.dot(direction);
        if pole.length()<1e-6 {pole=out[lower].w_axis.truncate()-a;pole-=direction*pole.dot(direction);}
        if pole.length()<1e-6 {let abs=direction.abs();let axis=if abs.x<=abs.y && abs.x<=abs.z {DVec3::X}else if abs.y<=abs.z {DVec3::Y}else{DVec3::Z};pole=axis-direction*axis.dot(direction);}
        pole= pole.normalize();let along=(l1*l1-l2*l2+d*d)/(2.*d);
        let knee=a+direction*along+pole*(l1*l1-along*along).max(0.).sqrt();
        desired[upper]=Some(arc(out[lower].w_axis.truncate()-a,knee-a)*rot(out[upper]));
        desired[lower]=Some(arc(out[end].w_axis.truncate()-out[lower].w_axis.truncate(),a+direction*d-knee)*rot(out[lower]));
    }
    out=hierarchy(rig,&desired,hips);
    out.extend(rig.board.iter().map(|i|source[*i as usize]));
    let root=unsafe{std::slice::from_raw_parts(root,16)};
    out.push(converted(DMat4::from_cols_array(root.try_into().unwrap())));
    let output=unsafe{std::slice::from_raw_parts_mut(output,no)};
    for (m,v) in out.into_iter().zip(output.chunks_exact_mut(10)) {pack(m,v);}
    if output.iter().any(|v|!v.is_finite()) {return 0;}1
}
