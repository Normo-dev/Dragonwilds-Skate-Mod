//! Grind rails found in the map's collision: the lips a skater can grind.
//!
//! MW2 collision comes from brushes, the clip mesh and static model collision,
//! so the top of a ledge and its wall rarely share an edge vertex for vertex.
//! A lip is therefore found by probing the geometry around each edge of a
//! walkable face rather than by matching triangles: the ground must fall away
//! just past the edge and nothing may rise there. Surviving edges are merged
//! along their lines across seams and T-junctions, then chained into
//! polylines where they meet at a gentle turn, as the authoring tool does for
//! edges marked sharp.

use std::collections::HashMap;

use bevy::math::Vec3;

/// A face this upward is walkable; its edges are rail candidates.
const UPWARD_Z: f32 = 0.65;
/// XY grid cell for probing, in map units (inches).
const CELL: f32 = 64.0;
/// How far past the lip the probes look.
const PROBE_OUT: f32 = 2.0;
/// Open space the ground must drop below the lip, just past it.
const MIN_DROP: f32 = 3.0;
/// Height above the lip the wall probe runs at.
const WALL_PROBE_UP: f32 = 3.0;
/// Shortest rail kept, after merging and chaining.
const MIN_RAIL: f32 = 24.0;
/// Steepest rail, as rise over length: stair handrails and ramp edges pass.
const MAX_SLOPE: f32 = 0.7;
/// Chains continue through a joint turning less than about 35 degrees.
const MIN_TURN_COS: f32 = 0.82;

pub struct RailCensus {
    pub candidates: usize,
    pub lips: usize,
    pub runs: usize,
    pub rails: usize,
}

/// Retain the first authored face behind each eligible edge so an immutable
/// world's lips can be re-probed after host geometry is added or removed.
#[derive(Clone, Copy, Debug)]
pub struct EdgeCandidate {
    pub owner: usize,
    pub a: Vec3,
    pub b: Vec3,
    pub normal: Vec3,
    pub centroid: Vec3,
}
pub type EdgeKey = ([i32;3], [i32;3]);
pub fn edge_key(a:Vec3,b:Vec3)->EdgeKey {
    let key=|v:Vec3|v.to_array().map(|x|(x*8.).round() as i32);
    let (a,b)=(key(a),key(b));if a<b {(a,b)} else {(b,a)}
}
pub fn face_candidates(owner:usize,tri:[Vec3;3])->impl Iterator<Item=(EdgeKey,EdgeCandidate)> {
    let normal=(tri[1]-tri[0]).cross(tri[2]-tri[0]).normalize_or_zero();
    let centroid=(tri[0]+tri[1]+tri[2])/3.;
    (0..3).filter(move |_|normal.z>UPWARD_Z).map(move |i|{
        let (a,b)=(tri[i],tri[(i+1)%3]);
        (edge_key(a,b),EdgeCandidate{owner,a,b,normal,centroid})
    })
}
/// Original eligibility and lip predicates with caller-owned broadphase.
/// The callback must test the same two-sided ray/triangle predicate globally.
pub fn candidate_is_lip(edge:EdgeCandidate, mut hit:impl FnMut(Vec3,Vec3,f32)->bool)->bool {
    let EdgeCandidate{a,b,normal,centroid,..}=edge;
    let along=b-a;let len=along.length();
    if len<1. || along.z.abs()>MAX_SLOPE*len {return false;}
    let mid=(a+b)*0.5;let mut out=along.cross(normal).normalize_or_zero();
    if out.dot(centroid-mid)>0. {out=-out;}
    let samples:&[f32]=if len<12. {&[0.5]} else {&[0.25,0.5,0.75]};
    let passing=samples.iter().filter(|&&s|{
        let p=a+along*s;
        let down_from=p+out*PROBE_OUT+Vec3::Z;
        if hit(down_from,-Vec3::Z,1.+MIN_DROP){return false;}
        let across_from=p-out+Vec3::Z*WALL_PROBE_UP;
        !hit(across_from,out,2.+PROBE_OUT)
    }).count();
    passing*3>=samples.len()*2
}
pub fn finish_lips(lips:Vec<(Vec3,Vec3)>,candidates:usize)->(Vec<Vec<Vec3>>,RailCensus) {
    let lip_count=lips.len();let runs=merge_collinear(lips);let run_count=runs.len();
    let mut rails=chain(runs);rails.retain(|rail|polyline_len(rail)>=MIN_RAIL);
    let census=RailCensus{candidates,lips:lip_count,runs:run_count,rails:rails.len()};
    (rails,census)
}

/// Rails over `tris` (map units, z up), each a polyline of two or more points.
pub fn find(tris: &[[Vec3; 3]]) -> (Vec<Vec<Vec3>>, RailCensus) {
    let grid = Grid::build(tris);
    let mut probe = Probe {
        tris,
        grid: &grid,
        stamp: vec![0; tris.len()],
        round: 0,
        scratch: Vec::new(),
    };

    // Every edge of a walkable face, once.
    let mut edges: HashMap<EdgeKey,EdgeCandidate> = HashMap::new();
    for (owner,&tri) in tris.iter().enumerate() {
        for (key,edge) in face_candidates(owner,tri) {edges.entry(key).or_insert(edge);}
    }
    let candidates = edges.len();

    let mut lips = Vec::new();
    for edge in edges.into_values() {
        if candidate_is_lip(edge,|o,d,l|probe.hit(o,d,l)) {lips.push((edge.a,edge.b));}
    }
    finish_lips(lips,candidates)
}

fn polyline_len(points: &[Vec3]) -> f32 {
    points.windows(2).map(|w| w[0].distance(w[1])).sum()
}

struct Grid {
    cells: HashMap<(i32, i32), Vec<u32>>,
    // The XY grid also contains faces on distant storeys or far above ground.
    // Conservative height bounds reject those before the unchanged ray test.
    z_bounds: Vec<(f32, f32)>,
    // Large host faces must not allocate a cell entry for every square metre
    // of their bounds. Probes still test these same faces after a bounds check.
    large: Vec<(u32, Vec3, Vec3)>,
}

fn cell_of(v: f32) -> i32 {
    (v / CELL).floor() as i32
}

impl Grid {
    fn build(tris: &[[Vec3; 3]]) -> Self {
        let mut cells: HashMap<(i32, i32), Vec<u32>> = HashMap::new();
        let mut large = Vec::new();
        let mut z_bounds = Vec::with_capacity(tris.len());
        for (index, tri) in tris.iter().enumerate() {
            let min = tri[0].min(tri[1]).min(tri[2]);
            let max = tri[0].max(tri[1]).max(tri[2]);
            let padding = 1.0 + min.z.abs().max(max.z.abs()) * f32::EPSILON * 16.;
            z_bounds.push((min.z - padding, max.z + padding));
            let nx=i64::from(cell_of(max.x))-i64::from(cell_of(min.x))+1;
            let ny=i64::from(cell_of(max.y))-i64::from(cell_of(min.y))+1;
            if nx.saturating_mul(ny)>4096 {
                large.push((index as u32,min,max));
                continue;
            }
            for x in cell_of(min.x)..=cell_of(max.x) {
                for y in cell_of(min.y)..=cell_of(max.y) {
                    cells.entry((x, y)).or_default().push(index as u32);
                }
            }
        }
        Self { cells, z_bounds, large }
    }
}

struct Probe<'a> {
    tris: &'a [[Vec3; 3]],
    grid: &'a Grid,
    stamp: Vec<u32>,
    round: u32,
    scratch: Vec<u32>,
}

#[cfg(test)]
mod host_grid_tests {
    use super::*;
    #[test]
    fn retains_all_eligible_ledges_beyond_uint16_rail_count() {
        // Each isolated triangular platform contributes three eligible lips.
        // The finder must retain them all; temporary native spline tables are
        // partitioned by their consumer without discarding shorter ledges.
        let tris: Vec<_> = (0..22_000).map(|i| {
            let a = Vec3::new((i % 200) as f32 * 128., (i / 200) as f32 * 128., 0.);
            [a, a + Vec3::X * 32., a + Vec3::Y * 32.]
        }).collect();
        let (rails, census) = find(&tris);
        assert_eq!(rails.len(), 66_000);
        assert_eq!(census.rails, 66_000);
        assert!(rails.iter().all(|rail| polyline_len(rail) >= MIN_RAIL));
    }

    #[test]
    fn large_face_probes_match_the_original_triangle_predicate() {
        let tris=[
            [Vec3::new(-20000.,-20000.,0.),Vec3::new(20000.,-20000.,0.),Vec3::new(0.,20000.,0.)],
            [Vec3::new(0.,0.,10.),Vec3::new(128.,0.,10.),Vec3::new(0.,128.,10.)],
        ];
        let grid=Grid::build(&tris);
        assert_eq!(grid.large.len(),1);
        assert!(grid.cells.len()<100);
        let mut probe=Probe{tris:&tris,grid:&grid,stamp:vec![0;tris.len()],round:0,scratch:vec![]};
        let mut seed=7u32;
        for _ in 0..1000 {
            let mut random=|| {seed=seed.wrapping_mul(1664525).wrapping_add(1013904223);seed as f32/u32::MAX as f32};
            let origin=Vec3::new((random()-0.5)*50000.,(random()-0.5)*50000.,100.);
            let expected=tris.iter().any(|t|ray_triangle(origin,-Vec3::Z,200.,t));
            assert_eq!(probe.hit(origin,-Vec3::Z,200.),expected);
        }
    }

    #[test]
    fn height_rejection_matches_original_predicate_for_stacked_and_offset_faces() {
        let mut seed=31u32;
        let mut random=|| {seed=seed.wrapping_mul(1664525).wrapping_add(1013904223);seed as f32/u32::MAX as f32};
        let mut tris=Vec::new();
        for _ in 0..600 {
            let a=Vec3::new(random()*120.,random()*120.,(random()-0.5)*8000.);
            tris.push([a,a+Vec3::new(random()*80.,random()*80.,random()*4.),a+Vec3::new(random()*80.,random()*80.,random()*4.)]);
        }
        // Include large absolute heights to exercise the ULP allowance.
        for &height in &[0.,100_000.,-100_000.,4_000_000.] {
            tris.push([Vec3::new(0.,0.,height),Vec3::new(120.,0.,height),Vec3::new(0.,120.,height)]);
        }
        let grid=Grid::build(&tris);
        let mut probe=Probe{tris:&tris,grid:&grid,stamp:vec![0;tris.len()],round:0,scratch:vec![]};
        for _ in 0..3000 {
            let origin=Vec3::new(random()*200.,random()*200.,(random()-0.5)*9000.);
            let dir=Vec3::new(random()-0.5,random()-0.5,random()-0.5).normalize_or_zero();
            let len=random()*60.;
            assert_eq!(probe.hit(origin,dir,len),tris.iter().any(|t|ray_triangle(origin,dir,len,t)));
        }
        for tri in &tris {
            let centre=(tri[0]+tri[1]+tri[2])/3.;
            for &offset in &[0.01,0.99,1.01,4.,40.] {
                let origin=centre+Vec3::Z*offset;
                let expected=tris.iter().any(|t|ray_triangle(origin,-Vec3::Z,offset*2.,t));
                assert_eq!(probe.hit(origin,-Vec3::Z,offset*2.),expected);
            }
        }
    }
}

impl Probe<'_> {
    /// Past `p` along `out` the ground falls away and nothing rises.
    fn is_lip(&mut self, p: Vec3, out: Vec3) -> bool {
        let down_from = p + out * PROBE_OUT + Vec3::Z;
        if self.hit(down_from, -Vec3::Z, 1. + MIN_DROP) {
            return false;
        }
        let across_from = p - out + Vec3::Z * WALL_PROBE_UP;
        !self.hit(across_from, out, 2. + PROBE_OUT)
    }

    /// Whether the segment from `origin` along unit `dir` for `len` crosses
    /// any triangle, either side facing.
    fn hit(&mut self, origin: Vec3, dir: Vec3, len: f32) -> bool {
        let end = origin + dir * len;
        let (min, max) = (origin.min(end), origin.max(end));
        let z_padding = min.z.abs().max(max.z.abs()) * f32::EPSILON * 16.;
        let (min_z, max_z) = (min.z - z_padding, max.z + z_padding);
        self.round = self.round.wrapping_add(1);
        if self.round == 0 {
            self.stamp.fill(0);
            self.round = 1;
        }
        self.scratch.clear();
        for x in cell_of(min.x)..=cell_of(max.x) {
            for y in cell_of(min.y)..=cell_of(max.y) {
                let Some(cell) = self.grid.cells.get(&(x, y)) else {
                    continue;
                };
                for &index in cell {
                    let seen = &mut self.stamp[index as usize];
                    if *seen != self.round {
                        *seen = self.round;
                        let (low, high) = self.grid.z_bounds[index as usize];
                        if low <= max_z && high >= min_z {
                            self.scratch.push(index);
                        }
                    }
                }
            }
        }
        for &(index, low, high) in &self.grid.large {
            if low.cmple(max).all() && high.cmpge(min).all() {
                self.scratch.push(index);
            }
        }
        self.scratch
            .iter()
            .any(|&index| ray_triangle(origin, dir, len, &self.tris[index as usize]))
    }
}

/// Möller–Trumbore, both faces, hits within `(0, len]`.
pub fn ray_triangle(origin: Vec3, dir: Vec3, len: f32, tri: &[Vec3; 3]) -> bool {
    let e1 = tri[1] - tri[0];
    let e2 = tri[2] - tri[0];
    let p = dir.cross(e2);
    let det = e1.dot(p);
    if det.abs() < 1e-8 {
        return false;
    }
    let inv = 1. / det;
    let s = origin - tri[0];
    let u = s.dot(p) * inv;
    if !(0. ..=1.).contains(&u) {
        return false;
    }
    let q = s.cross(e1);
    let v = dir.dot(q) * inv;
    if v < 0. || u + v > 1. {
        return false;
    }
    let t = e2.dot(q) * inv;
    t > 1e-4 && t <= len
}

/// Lips on one line, merged into maximal runs where they overlap or touch.
fn merge_collinear(lips: Vec<(Vec3, Vec3)>) -> Vec<(Vec3, Vec3)> {
    let mut lines: HashMap<([i32; 3], [i32; 3]), (Vec3, Vec3, Vec<(f32, f32)>)> = HashMap::new();
    for (mut a, mut b) in lips {
        let mut d = (b - a).normalize_or_zero();
        if d == Vec3::ZERO {
            continue;
        }
        let flip =
            d.x < -1e-4 || (d.x.abs() <= 1e-4 && (d.y < -1e-4 || (d.y.abs() <= 1e-4 && d.z < 0.)));
        if flip {
            d = -d;
            std::mem::swap(&mut a, &mut b);
        }
        let o = a - d * a.dot(d);
        let k = (
            (d * 64.).to_array().map(|x| x.round() as i32),
            (o * 2.).to_array().map(|x| x.round() as i32),
        );
        let line = lines.entry(k).or_insert((d, o, Vec::new()));
        line.2.push((a.dot(line.0), b.dot(line.0)));
    }
    let mut runs = Vec::new();
    for (d, o, mut spans) in lines.into_values() {
        spans.sort_by(|x, y| x.0.total_cmp(&y.0));
        let mut current = spans[0];
        for &(t0, t1) in &spans[1..] {
            if t0 <= current.1 + 1.5 {
                current.1 = current.1.max(t1);
            } else {
                runs.push((o + d * current.0, o + d * current.1));
                current = (t0, t1);
            }
        }
        runs.push((o + d * current.0, o + d * current.1));
    }
    runs
}

/// Runs joined end to end into polylines through joints where exactly two
/// runs meet at a gentle turn.
fn chain(runs: Vec<(Vec3, Vec3)>) -> Vec<Vec<Vec3>> {
    let node = |v: Vec3| v.to_array().map(|x| x.round() as i32);
    let mut at: HashMap<[i32; 3], Vec<(usize, bool)>> = HashMap::new();
    for (i, (a, b)) in runs.iter().enumerate() {
        at.entry(node(*a)).or_default().push((i, false));
        at.entry(node(*b)).or_default().push((i, true));
    }
    let mut used = vec![false; runs.len()];
    let far = |i: usize, from_end: bool| if from_end { runs[i].0 } else { runs[i].1 };
    let near = |i: usize, from_end: bool| if from_end { runs[i].1 } else { runs[i].0 };
    // The run continuing a polyline at `joint`, arriving along `heading`.
    let next = |joint: Vec3, heading: Vec3, used: &[bool]| -> Option<(usize, bool)> {
        let there = at.get(&node(joint))?;
        if there.len() != 2 {
            return None;
        }
        let &(i, at_end) = there.iter().find(|(i, _)| !used[*i])?;
        let leave = (far(i, at_end) - near(i, at_end)).normalize_or_zero();
        (heading.dot(leave) >= MIN_TURN_COS).then_some((i, at_end))
    };
    let mut rails = Vec::new();
    for start in 0..runs.len() {
        if used[start] {
            continue;
        }
        used[start] = true;
        let (a, b) = runs[start];
        let mut points = std::collections::VecDeque::from([a, b]);
        // Forward from b, then backward from a.
        let mut tip = b;
        let mut heading = (b - a).normalize_or_zero();
        while let Some((i, at_end)) = next(tip, heading, &used) {
            used[i] = true;
            let to = far(i, at_end);
            heading = (to - tip).normalize_or_zero();
            tip = to;
            points.push_back(to);
        }
        let mut tip = a;
        let mut heading = (a - b).normalize_or_zero();
        while let Some((i, at_end)) = next(tip, heading, &used) {
            used[i] = true;
            let to = far(i, at_end);
            heading = (to - tip).normalize_or_zero();
            tip = to;
            points.push_front(to);
        }
        rails.push(points.into_iter().collect());
    }
    rails
}
