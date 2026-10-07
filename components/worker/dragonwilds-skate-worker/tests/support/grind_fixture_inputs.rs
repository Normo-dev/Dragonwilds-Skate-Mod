use super::{Cells, edge, straight};

pub fn cells() -> Cells {
    let mut star = Vec::new();
    for i in 0..8 {
        let angle = i as f64 * std::f64::consts::FRAC_PI_4;
        star.push(edge(
            [1000., -0., -0.],
            [1000. + 100. * angle.cos(), 100. * angle.sin(), -0.],
        ));
    }
    vec![
        ([0, 0, 0], "line".into(), straight(0., 5)),
        ([1, 0, 0], "branches".into(), star),
        ([2, 0, 0], "short".into(), straight(3000., 1)),
        (
            [3, 0, 0],
            "single".into(),
            vec![edge([4000., -0., -0.], [4100., -0., -0.])],
        ),
        ([4, 0, 0], "shared".into(), straight(0., 5)),
        (
            [5, 0, 0],
            "curve".into(),
            vec![
                edge([5000., 0., 0.], [5030., 0., 0.]),
                edge([5030., 0., 0.], [5030., 30., 0.]),
            ],
        ),
    ]
}
pub fn changed(mut cells: Cells) -> Cells {
    cells[1].1 = "branch-removal".into();
    cells[1].2.truncate(2);
    cells[2].1 = "short-now-retained".into();
    cells[2].2 = straight(3000., 3);
    cells[3].1 = "new-single-reusing-slot".into();
    cells[3].2 = vec![edge([6000., -0., -0.], [6100., -0., -0.])];
    cells
}
pub fn extended(mut cells: Cells) -> Cells {
    cells[2].1 = "short-extended".into();
    cells[2].2 = straight(3000., 3);
    cells.push((
        [9, 0, 0],
        "added".into(),
        vec![edge([7000., -0., -0.], [7100., -0., -0.])],
    ));
    cells
}
