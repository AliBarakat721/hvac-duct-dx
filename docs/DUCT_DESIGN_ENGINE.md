# Preliminary Duct Design Engine

## Implemented calculation scope

The engine sizes each entered rectangular duct section independently for one constant airflow. The user provides the flow, straight length, an aggregate fitting-loss coefficient ΣK, a maximum velocity, a maximum straight-duct friction rate, allowable width and height ranges, and a discrete size step. The engine searches the permitted grid and returns the feasible section with the smallest cross-sectional area. Ties prefer a more nearly square aspect ratio, then the lower modeled section loss.

The engine does not infer a topology from the list. Results for two rows are not added together and are not a system or fan pressure requirement. Users must not interpret the output as a balanced network design.

## Equations and units

The UI accepts CFM, feet, inches, inches water gauge, °C, kPa absolute, and millimetres of absolute roughness. The calculation converts these inputs to SI internally.

For a rectangular section of width `W` and height `H`, the engine calculates the Huebscher circular-equivalent diameter:

```text
De = 1.30 (W H)^0.625 / (W + H)^0.25
```

The same length unit must be used for both dimensions. This correlation represents the round duct diameter associated with equal airflow and approximate equal friction for a rectangular section; it is not the hydraulic diameter. The hydraulic diameter `Dh = 2 W H / (W + H)` is also returned as a geometry diagnostic but is not substituted for `De` in the equal-friction sizing calculation. The local SMACNA extract says to select rectangular sizes through circular-equivalent tables rather than by matching cross-sectional area alone.

Air density uses the ideal-gas equation `ρ = p/(R T)` for dry air. Dynamic viscosity uses Sutherland's relation. Reynolds number and the Darcy friction factor are calculated for the equivalent round section. Laminar flow uses `fD = 64/Re`; turbulent flow solves Colebrook–White by bracketed bisection:

```text
1 / √fD = −2 log10( ε/(3.7 De) + 2.51/(Re √fD) )
Δpstraight = fD (L/De) (ρ Veq²/2)
```

The 2300–4000 Reynolds transition is bridged linearly between the laminar value at 2300 and the Colebrook value at 4000. This is an explicit numerical engineering decision, not a SMACNA acceptance rule; it is flagged as `REVIEW` in the UI. Typical HVAC duct flows are expected to be turbulent.

The actual rectangular-section mean velocity is `Vrect = Q/(W H)`. Fitting loss is calculated separately as `Δpfittings = ΣK (ρ Vrect²/2)`, with ΣK supplied by the user and referenced to that segment's actual rectangular velocity. Total modeled section loss is the sum of straight friction and these entered K losses. The UI does not select fitting K values or convert equivalent lengths.

The default galvanized-steel roughness is 0.09 mm, matching the standard clean galvanized duct basis described in the SMACNA text excerpt already stored in `extracted/smacna_duct_design.txt` (Chapter 5, printed pp. 5.9–5.12). The same excerpt gives standard-air density as about 1.204 kg/m³ and expresses dynamic fitting loss as a coefficient multiplied by velocity pressure. Roughness, temperature, and absolute pressure remain editable; changes require an engineering basis.

## Selection and validation

A size is feasible only if both of these user-selected screening constraints are met:

- actual rectangular-section velocity does not exceed the configured maximum;
- equivalent-round straight-duct friction does not exceed the configured maximum friction rate.

All flows, lengths and size criteria must be finite and positive; ΣK and roughness may be zero but not negative. Width and height ranges must be ordered. Tags must be unique. If no grid size meets both criteria, the engine returns a clear input/sizing error instead of silently using an invalid size.

## Engineering limitations

This is a preliminary segment-sizing tool, not a complete HVAC system designer and not a statement of SMACNA compliance. It does not model connected paths, branching flow conservation, critical-path pressure, fan/system effects, pressure regain, balancing dampers, fitting geometry or fitting-specific K selection, leakage, duct gauge, reinforcement, transverse joints, sealing class, hangers, insulation, service clearances, round/oval/flexible ducts, acoustic performance, or terminal pressure spread. It also does not round to a project-approved commercial schedule beyond the user-selected grid. The reported `PASS — screening only` means only that the entered segment met the two selected numerical constraints.

Before using results for construction or equipment selection, an HVAC engineer must verify the project criteria, segment network, duct construction class, fitting coefficients, pressure reference and locally adopted standards. Compare sizing against the project's licensed SMACNA/ASHRAE tables and design procedures.

## References

- Darcy–Weisbach friction pressure loss and hydraulic-diameter definitions: [1].
- Rectangular-to-circular equivalent diameter correlation attributed to Huebscher: [2].
- Colebrook–White friction relation and distinction between Darcy and Fanning factors: [3].
- SMACNA Duct Design, Chapters 5 and 14, source excerpt in this repository at `extracted/smacna_duct_design.txt`; printed pages 5.9–5.12 discuss straight friction, standard-air basis, galvanized-steel roughness, fitting loss coefficients, and circular equivalents.

[1]: https://www.engineeringtoolbox.com/darcy-weisbach-equation-d_646.html "Darcy-Weisbach Equation Flow Resistance and Pressure Loss"
[2]: https://www.engineeringtoolbox.com/equivalent-diameter-d_205.html "Fluid Flow Equivalent Diameter"
[3]: https://en.wikipedia.org/wiki/Darcy%E2%80%93Weisbach_equation "Darcy-Weisbach Equation and Colebrook-White Relation"
