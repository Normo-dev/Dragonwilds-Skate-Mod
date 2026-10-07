#[path="../src/compact_world.rs"] mod compact_world;
#[path="../src/compact_geometry.rs"] mod compact_geometry;
#[path="../src/compact_rails.rs"] mod compact_rails;
#[path="../../skate3-mashup/crates/render_anim/src/skate/rails.rs"] mod example_rails;
use std::{path::PathBuf,sync::Arc};
use compact_world::CompactWorld;
use compact_geometry::CompactGeometry;

#[test]
fn identical_live_replacement_preserves_every_lip_coordinate_and_input_position(){
    let root=PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../compact-world-fixture");
    let base=Arc::new(CompactWorld::load(&root.join("export/manifest.json")).unwrap());
    let source=CompactGeometry::new(base.clone(),[0.;3]).unwrap();
    let original=compact_rails::bake(&source).unwrap();
    assert!(!original.lips.is_empty());
    let view=Arc::new(CompactWorld::with_overlay(&base,&root.join("overlay-noop/manifest.json")).unwrap());
    let updated=compact_rails::overlay(&original,&CompactGeometry::new(view,[0.;3]).unwrap()).unwrap();
    assert_eq!(updated.lips.len(),original.lips.len());
    for (a,b) in original.lips.iter().zip(&updated.lips){
        assert_eq!(a.a,b.a);assert_eq!(a.b,b.b);assert_eq!(a.normal,b.normal);assert_eq!(a.centroid,b.centroid);
    }
}
