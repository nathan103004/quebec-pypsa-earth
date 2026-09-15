# Debugging history

Issues found and fixed during development of the scripts in `network/`. Code
comments describe current behavior only; this file keeps the reasoning and
the concrete cases that motivated each fix.

## `reduce_voltage_network.py`

**Bridge-bus fragmentation.** Early versions folded every sub-315kV local
bus onto its nearest backbone bus and dropped it. This is wrong when a local
bus is the *only* electrical path between two backbone buses: bus 328
(230kV) was the sole connection between backbone buses 329 and 330, via a
transformer to 330 and a Link to 329 -- neither counted as "backbone" by
voltage. Dropping it fragmented the network into disconnected islands even
though both backbone endpoints were nominally kept.

Fix (later reverted, see below): detect local buses that are articulation
points separating >=2 backbone buses (via `networkx.articulation_points`),
and keep them instead of folding them. This generalized beyond the one
originally-found case -- it also caught buses 1314, 1337, and 232, and
fixed the Gaspé island as a side effect, none of which had been found by
the original manual patch.

**Load consolidation.** Reassigning a local bus's load onto its nearest
backbone bus didn't merge it with other loads landing on the same target --
one backbone bus (1810) ended up hosting 60 separate Load rows. Fixed by
summing all loads sharing a bus into one aggregate Load per bus, matching
the convention `reduce_to_735kv.py` already used.

**Both fixes were reverted** in an attempt to reproduce an AC PF result
that had converged on the pre-fix network topology. Reverting did not
restore that convergence (see "AC PF convergence investigation" below) --
the fixes are not implicated in the AC PF issue and are safe to keep.

## `reduce_to_735kv.py`

**Near-singular bridge-line admittance.** The transformer bridging the
735kV/765kV tier (`transf_55_3`) has a per-unit reactance of ~5e-5 -- a
near-short-circuit value once transformer `s_nom` was flattened to 100,000
MVA elsewhere in the pipeline. Converting this literally into an equivalent
Line's ohms gave a bridge line with susceptance B=2,000,000,000, vs.
3,144-324,200 for every real 735kV line in the fleet -- a near-singular
Ybus entry. This alone drove AC PF to fail on 289/336 snapshots with errors
~1e44+.

Fix: derive the bridge line's reactance from the 735kV fleet's own average
ohm/km, over a short nominal length (6km, matching the fleet's shortest
real line) instead of from the transformer's literal per-unit value. This
was necessary but not sufficient -- convergence was unchanged (47/336)
immediately after this fix alone; the dominant cause turned out to be
elsewhere (see AC PF section below).

**Straight-line vs. graph-distance reassignment.** `reduce_voltage_network.py`
reassigns buses by straight-line (Euclidean) nearest-neighbor. This method
produced two confirmed real misassignments earlier in this project: 57
substations reassigned to a bus up to 257km away, and 25 Ottawa-area loads
assigned to a Quebec bus that should never have gotten them (both due to
provincial-border geography defeating a purely geographic distance metric).
`reduce_to_735kv.py` uses graph shortest-path (real cumulative line length)
instead, specifically to avoid repeating this.

## `run_pf.py`

**Slack-bus bug.** `prepare_for_ac_pf()` resets every generator's control
to "PQ" before promoting some to PV (`n.generators["control"] = "PQ"`).
Without explicitly preserving the prior Slack flag across that reset,
PyPSA's own fallback (`find_slack_bus()`, which picks `gens.index[0]` when
no generator is marked Slack) auto-selects essentially by index order, with
no carrier filter. In one run this landed on `1016 load_shedding` -- a
$10,000/MWh placeholder sized to local peak load, forced to absorb the
entire AC solve's real+reactive residual as if it were a real generator.
Every AC PF run in this project used this same unintended fallback slack
until the bug was found (while investigating a spurious "shedding" artifact
on a results map) and fixed by capturing `prior_slack` before the reset and
restoring it afterward.

**Reactive capability tied to nameplate, not dispatch.** Found via bus 1810
(see above -- it aggregates 57 real substations onto one radial 315kV tie
with 3 local ror generators). Before this fix, those generators had zero
reactive capability whenever they were idle, so all of that bus's Q demand
had to route through the single tie with no local support. Tying Q
capability to installed capacity (`p_nom`) instead of real-time dispatch
fixes this -- a real synchronous machine can supply close to its full Q
capability even at zero real output (how synchronous condensers work).

**PV eligibility thresholds are topology-dependent.** Marking every
generator bus PV made `n.pf()` diverge on every snapshot on the 208-bus
`elec_solved.nc` network -- PyPSA's PV buses hold voltage with unlimited
reactive power, no capability limit, which is fine for one bus but
unstable for ~38 at once on a network with long, weak radial corridors.
The narrow default threshold (`p_nom >= 100 MW`, `load < 10%` of local
generation) was tuned for that network.

The 58-bus `elec_735kv.nc` network is smaller and more heavily meshed.
There, the *opposite* problem dominated: system-wide reactive "surplus"
(fixed Q supply from PQ generators, pinned to nameplate, minus real Q
demand) shrinks as the day's demand rises, narrowing Newton-Raphson's basin
of convergence at exactly the highest-demand hours. Opening PV eligibility
to every real generator bus there (`--pv-min-capacity 0 --pv-max-load-ratio 1`)
let the solver compute Q instead of fixing it, raising convergence from
43/336 to 334/336 on that network -- the single biggest lever found in the
whole AC PF investigation (see below).

## AC PF convergence investigation (`elec_735kv.nc`)

Full timeline, in order:

1. Baseline AC PF on the freshly-built 735kV network: 47/336 converged
   (2-week window), errors up to 1.8e63.
2. Bridge-line admittance fix (above): unchanged at 47/336 -- necessary,
   not sufficient.
3. Storage-only-bus PV-placeholder fix (`run_pf.py`, added so storage-only
   buses are PV-eligible): 43/336 -- slightly worse, ruling out "too few PV
   buses" as the sole explanation on its own.
4. Widened PV eligibility (`--pv-min-capacity 0 --pv-max-load-ratio 1`):
   334/336 -- the dominant fix. Diagnosed directly: in a 20-snapshot test,
   every converged hour had system-wide Q surplus >= 7,778 MVAr; every
   failed hour had <= 7,803 MVAr.
5. The same widened-PV fix, tried on the 208-bus `elec_solved.nc` network:
   0/168, complete failure -- reproduces the original "unstable for many PV
   buses on long radial corridors" finding. PV eligibility thresholds are
   genuinely topology-dependent, not a universal fix.
6. Slack-bus bug found and fixed (above). Re-running 735kV AC PF with the
   corrected slack dropped the reported convergence to 165/168 -- revealing
   the earlier "168/168" had been an artifact of the wrong (unrealistically
   flexible) fallback slack, not a real result.
7. Generator power-factor sensitivity test: PF=0.9 (realistic) -> 165/168;
   PF=0.6 (implies more reactive capability than real power rating, not
   physically defensible) -> 168/168. Rejected as the fix; too unrealistic.
8. Fixed shunt capacitors instead, sized to 70% of each bus's own peak
   reactive demand, generator PF left at realistic 0.9: 168/168, clean
   (60.5% max line loading, 0 lines >=90%). Adopted as the working recipe.
   Known trade-off: voltage magnitude spread widened (35/58 buses <0.95pu,
   34/58 >1.05pu, vs. 19/58 and 9/58 without shunts) -- a fixed (unswitched)
   capacitor bank over-compensates during low-demand hours.
9. Independent MATPOWER cross-check: exported the same (verified-equivalent)
   network and it did NOT converge in MATPOWER's `runpf()`, despite DC PF
   matching PyPSA exactly (branch flows agreed to the decimal). Seven
   separate hypotheses were tested and ruled out (flat start, Q-limit
   enforcement, iteration count, branch/shunt data errors, line-charging
   susceptance on long corridors, PV voltage setpoints). Conclusion: a
   genuine algorithmic robustness difference between PyPSA's and
   MATPOWER's Newton-Raphson implementations on this data, not a data bug.
10. `elec_reduced.nc` was regenerated from scratch (bridge-bus + load-
    consolidation fixes above) and the 735kV network rebuilt from it. AC PF
    regressed to 53/168 with the same recipe. A perfectly-tracking local
    reactive-power source (zero-P generator per bus, `q_set` set to exactly
    match local hourly demand) was tested to isolate the mechanism: 67/168
    -- ruling out the Q-budget mechanism as the *sole* explanation. Real
    power / angle stress (DC-seeded angle spread averaged 117 deg on failed
    snapshots vs. 95 deg on converged ones) was identified as a second,
    independent contributing factor.
11. Comparing the regenerated network against a recovered pre-regeneration
    copy (`elec_735kv_pf_bcapped.nc`, saved during step 9's diagnostics)
    found 4 corridors with fewer real parallel circuits in the new build
    (6 fewer lines total: 115 -> 109), concentrated at the two most
    electrically stressed buses in the network. Checking against
    `elec_full.nc` (the unchanged raw source) showed the *old* network's
    higher circuit counts didn't match the real source data either --
    they came from an upstream process (likely `fix_parallel_circuits_v2.py`,
    whose own source data files no longer exist in the repo) that was never
    actually reflected in the current `elec_full.nc`. The recovered 168/168
    network is real and reproducible, but its convergence is partly
    supported by non-real transmission capacity at exactly its two most
    stressed nodes. This tension (accurate-but-non-convergent vs.
    convergent-but-partly-fabricated) is unresolved.

**Current state**: `elec_735kv_pf.nc` holds the recovered 168/168 network
(115 lines, includes the non-real extra circuits above). A freshly-rebuilt,
fully-accurate version of the same network converges 71/168 with the same
recipe. Neither is a clean final answer.

## `export_to_matpower.py`

**Split per-unit conventions.** PyPSA stores line and transformer per-unit
values on different implicit bases (lines: 1 MVA; transformers: their own
`s_nom`) -- confirmed directly from `pypsa/pf.py`. Both must be rescaled to
MATPOWER's single system base (`--base-mva`, default 100) using the
standard `Z_pu_new = Z_pu_old * (S_new / S_old)` relation. Getting this
wrong for transformer resistance (leaving it at the source-data default of
r=0) caused MATPOWER's MIPS interior-point OPF solver to fail with
RCOND~2e-19 ("badly scaled") on an early export.

**Shunt capacitors and DC-seeded start were missing.** The export script
predates the shunt-capacitor fix in the AC PF investigation above, so early
exports silently carried zero shunt data and a flat (Va=0) starting angle
for every bus, instead of the DC-lpf-seeded angles `run_pf.py` uses. Both
were added: shunt susceptance is carried over unit-correctly (MATPOWER's
own `makeYbus.m` divides bus Gs/Bs by baseMVA internally, so the exported
value must be the raw MVAr, not pre-normalized -- confirmed by reading
MATPOWER's source directly), and bus `Va` is seeded from a fresh `n.lpf()`
solve at export time.

**Bus numbering is not deterministic.** MATPOWER bus IDs are assigned by
iterating a Python `set` of endpoint buses, which is not order-stable
across runs. Two exports of the same network can assign different MATPOWER
IDs to the same physical bus -- harmless for a single run, but exports
should not be diffed bus-by-bus across separate invocations.

## `run_lopf_main_island.py`

**Flat transformer capacity: found value matters, not just direction.**
Transformers came out of `reduce_voltage_network.py` with widely varying,
often small, `s_nom` values inherited from the raw OSM extract, several of
which sat directly at major junctions with 38,000-155,000 MVA of real line
capacity converging on them -- an artificial pinch point. Flattening every
transformer to one large `s_nom` fixed the LOPF-level bottleneck, but the
first value tried (raising `s_nom` without proportionally raising `x`, so
`x_pu = x/s_nom` collapsed toward zero) made AC PF's Newton-Raphson diverge
on every single snapshot -- a near-short-circuit admittance, the same class
of bug later re-found and fixed properly in `reduce_to_735kv.py`'s bridge
transformer (see above). Fix: scale `x` up proportionally with `s_nom` so
`x_pu` stays fixed at its original, real value; only the MVA rating changes.

**OCGT and ror/storage ceilings: exact-dispatch vs. ceiling-only.** Early
versions forced ror and OCGT generators to match their real historical
dispatch exactly (`p_min_pu = p_max_pu` from source data), which is overly
rigid for a re-optimization that's deliberately allowed to differ from
history (e.g. to cover higher scaled demand). Relaxed to ceiling-only
(`p_min_pu = 0`, real data only bounds `p_max_pu`), with `CEILING_MARGIN`
added on top since a hard ceiling at exactly the historical value leaves no
room to legitimately exceed history.

**Link 4349 (329-3975 DC tie) was undersized.** At its original `p_nom`,
this tie bound tightly enough to force load shedding upstream of it even
though real generation capacity existed to cover it -- tripling its
capacity was tested against smaller and larger multipliers; results were
insensitive to going further (`*6` gave identical shed to `*3`), so `*3`
was kept as the smallest multiplier that clears the bottleneck.

**Slack assignment mirrors `reduce_to_735kv.py`'s pattern, applied earlier
in the pipeline.** Same underlying PyPSA gap (`StorageUnit.control` is
never read by `find_bus_controls()`/`find_slack_bus()`, confirmed from
`pypsa/pf.py` source) and the same zero-dispatch-placeholder-Generator
workaround, but scoped to the largest AC-connected component (lines +
transformers only, no links) rather than the whole network, since this
runs before any voltage-tier reduction and the raw network still has
DC-link-isolated sub-islands that shouldn't be slack candidates.
