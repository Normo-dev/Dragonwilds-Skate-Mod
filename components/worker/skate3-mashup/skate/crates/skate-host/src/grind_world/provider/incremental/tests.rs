use super::*;

fn dense(count: usize) -> Arc<StaticProvider> {
    let rails: Vec<_> = (0..count)
        .map(|i| skate_data::skate_map::Rail {
            name: format!("iw4_edge_{i}"),
            closed: false,
            native: None,
            points: vec![[i as f32 * 0.1, 0., 0.], [i as f32 * 0.1, 0., 1.]],
        })
        .collect();
    let mut provider = StaticProvider::authored(&rails).unwrap();
    provider.enable_dense_authored().unwrap();
    Arc::new(provider)
}

#[test]
fn retired_owner_trie_is_persistent_and_handles_all_identifier_bits() {
    let values = [
        1,
        2,
        3,
        4,
        65_535,
        65_536,
        1 << 32,
        1 << 63,
        u64::MAX - 1,
        u64::MAX,
    ];
    let mut first = OwnerSet::default();
    for owner in values {
        first.insert(owner);
    }
    for owner in values {
        assert!(first.contains(owner), "{owner}");
    }
    for owner in [0, 5, 65_534, 65_537, (1 << 32) + 1] {
        assert!(!first.contains(owner));
    }
    let mut second = first.clone();
    second.insert(5);
    assert!(second.contains(5));
    assert!(!first.contains(5));
    second.insert(5);
    for owner in values {
        assert!(second.contains(owner));
    }
}

#[test]
fn retaining_the_global_native_base_preserves_initial_dense_query_parity() {
    let base = dense(160);
    let next = StaticProvider::exposed_delta(base.clone(), Vec::new(), Vec::new()).unwrap();
    assert!(Arc::ptr_eq(&next.incremental.as_ref().unwrap().base, &base));
    for (min, max) in [
        ([-1.; 3], [20.; 3]),
        ([2., -1., -1.], [8., 1., 2.]),
        ([8., -0.1, 0.], [8.2, 0.1, 0.5]),
    ] {
        assert_eq!(base.query(min, max).unwrap(), next.query(min, max).unwrap());
    }
    for id in 0..160 {
        let a = base.primitive(id).unwrap();
        let b = next.primitive(id).unwrap();
        assert_eq!((a.start, a.end, a.owner), (b.start, b.end, b.owner));
        assert_eq!(base.metadata(id), next.metadata(id));
    }
}

#[test]
fn replacement_retires_old_handles_without_changing_untouched_owners_or_storage() {
    let base = dense(100);
    let next = StaticProvider::exposed_delta(
        base.clone(),
        vec![11],
        vec![(101, vec![[1., 1., 0.], [1., 1., 1.], [1., 1., 2.]])],
    )
    .unwrap();
    assert!(next.primitive(10).is_none());
    assert!(next.metadata(10).is_none());
    assert!(next.spline_guids(11).is_none());
    assert!(base.primitive(10).is_some());
    assert_eq!(next.primitive_count(), 101);
    for id in [0, 9, 11, 99] {
        let a = base.primitive(id).unwrap();
        let b = next.primitive(id).unwrap();
        assert_eq!((a.start, a.end, a.owner), (b.start, b.end, b.owner));
    }
    assert_eq!(next.primitive(100).unwrap().owner, 101);
    assert_eq!(next.primitive(101).unwrap().owner, 101);
    assert_eq!(next.metadata(101).unwrap().segment_index, 1);
    assert!(next.spline_guids(101).is_some());
    let index = next.incremental.as_ref().unwrap();
    assert!(Arc::ptr_eq(&index.base, &base));
    assert_eq!(index.overlays.len(), 1);
    assert_eq!(index.overlays[0].provider.primitives.len(), 2);
    let moved = next.query([0.9, 0.9, -0.1], [1.1, 1.1, 2.1]).unwrap();
    assert_eq!(moved, [101, 100]);
}

#[test]
fn successive_overlay_updates_keep_active_storage_and_handle_generations_separate() {
    let base = dense(3);
    let first = StaticProvider::exposed_delta(
        base.clone(),
        vec![1],
        vec![(4, vec![[5., 0., 0.], [5., 0., 1.]])],
    )
    .unwrap();
    let second = StaticProvider::exposed_delta(
        first.clone(),
        vec![4],
        vec![(5, vec![[6., 0., 0.], [6., 0., 1.]])],
    )
    .unwrap();
    assert!(second.primitive(0).is_none());
    assert!(second.primitive(3).is_none());
    assert_eq!(second.primitive(4).unwrap().owner, 5);
    assert_eq!(second.primitive_count(), 3);
    assert!(first.primitive(3).is_some());
    assert!(Arc::ptr_eq(
        &second.incremental.as_ref().unwrap().base,
        &base
    ));
    assert_eq!(second.incremental.as_ref().unwrap().overlays.len(), 1);
    assert_eq!(second.incremental.as_ref().unwrap().overlays[0].first, 4);
    assert!(StaticProvider::exposed_delta(second.clone(), vec![1], Vec::new()).is_err());
    assert!(
        StaticProvider::exposed_delta(
            second.clone(),
            Vec::new(),
            vec![(5, vec![[0.; 3], [1.; 3]])]
        )
        .is_err()
    );
    assert_eq!(second.primitive(4).unwrap().owner, 5);
}

#[test]
fn global_nearest_forty_includes_overlays_and_emits_retained_native_order() {
    let base = dense(80);
    let before = base.query([-1.; 3], [10.; 3]).unwrap();
    let next = StaticProvider::exposed_delta(
        base,
        vec![1],
        vec![(81, vec![[4.5, 4.5, 4.5], [4.5, 4.5, 5.5]])],
    )
    .unwrap();
    let selected = next.query([-1.; 3], [10.; 3]).unwrap();
    assert_eq!(selected.len(), 40);
    assert!(selected.contains(&80));
    assert_eq!(selected.last(), Some(&80));
    let retained: Vec<_> = selected.iter().copied().filter(|id| *id < 80).collect();
    let previous: Vec<_> = before
        .into_iter()
        .filter(|id| retained.contains(id))
        .collect();
    assert_eq!(retained, previous);
}

#[test]
fn handplant_source_order_and_uncapped_footplant_both_use_spatial_candidates() {
    let base = dense(100);
    let next = StaticProvider::exposed_delta(
        base.clone(),
        vec![2],
        vec![(101, vec![[1., 0., 0.], [1., 0., 1.]])],
    )
    .unwrap();
    let hand = next
        .query_source_order([-1.; 3], [20.; 3], Some(40))
        .unwrap();
    assert_eq!(hand.len(), 40);
    assert_eq!(
        hand.iter().map(|p| p.owner).collect::<Vec<_>>(),
        (1..=41).filter(|id| *id != 2).collect::<Vec<_>>()
    );
    let foot = next.query_source_order([-1.; 3], [20.; 3], None).unwrap();
    assert_eq!(foot.len(), 100);
    assert_eq!(foot.last().unwrap().owner, 101);
    let local = next
        .query_source_order([0.99, -0.1, -0.1], [1.01, 0.1, 1.1], None)
        .unwrap();
    assert_eq!(local.iter().map(|p| p.owner).collect::<Vec<_>>(), [11, 101]);
}

#[test]
fn an_explicit_empty_exposed_provider_accepts_first_rail_and_full_removal() {
    let mut empty = StaticProvider::new(None).unwrap();
    empty.enable_dense_authored().unwrap();
    let added = StaticProvider::exposed_delta(
        Arc::new(empty),
        Vec::new(),
        vec![(1, vec![[0.; 3], [0., 0., 1.]])],
    )
    .unwrap();
    assert_eq!(added.primitive_count(), 1);
    assert_eq!(added.primitive(0).unwrap().owner, 1);
    let removed = StaticProvider::exposed_delta(added.clone(), vec![1], Vec::new()).unwrap();
    assert_eq!(removed.primitive_count(), 0);
    assert!(removed.primitive(0).is_none());
    assert!(removed.query([-1.; 3], [2.; 3]).unwrap().is_empty());
    assert!(added.primitive(0).is_some());
}

#[test]
#[ignore = "Offline provider update timing; parent coordinates Cargo"]
fn small_scene_edit_decodes_only_touched_overlay_on_a_large_retained_base() {
    let base = dense(200_000);
    let start = std::time::Instant::now();
    let next = StaticProvider::exposed_delta(
        base.clone(),
        vec![10],
        vec![(200_001, vec![[1., 1., 0.], [1., 1., 1.]])],
    )
    .unwrap();
    let elapsed = start.elapsed();
    let index = next.incremental.as_ref().unwrap();
    assert!(Arc::ptr_eq(&index.base, &base));
    assert_eq!(index.overlays[0].provider.primitives.len(), 1);
    assert_eq!(next.primitive_count(), 200_000);
    eprintln!(
        "INCREMENTAL_PROVIDER_BENCH base_segments=200000 decoded_segments=1 update_us={}",
        elapsed.as_micros()
    );
}
