import cadquery as cq

result = (
    cq.Workplane("XZ")
    .polyline([(0, 0), (40, 0), (40, 4), (4, 4), (4, 34), (0, 34)])
    .close()
    .extrude(30)
    .edges(cq.NearestToPointSelector((4, 15, 4)))
    .fillet(3)
)

holes = (
    cq.Workplane("XY")
    .pushPoints([(22, 7.5), (22, 22.5)])
    .circle(2)
    .extrude(10)
)

result = result.cut(holes)
