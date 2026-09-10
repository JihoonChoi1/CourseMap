"""Unit graph construction, is_binding, ES/LS/tail."""

import unittest

from helpers import F, S, catalog, course, programs
from engine.graph import (build_unit_graph, compute_descendants, compute_es, compute_ls, compute_tail,
                          is_binding)
from engine.model import Calendar
from engine.validate import validate


def build(courses, ids=None, completed=None, start="FALL", num_terms=8):
    cat = catalog(courses)
    static = validate(cat, programs([]))
    assert static.errors == [], static.errors
    done = {}
    for cid in completed or []:
        done[cid] = True
    if ids is None:
        ids = [c.id for c in courses if c.id not in done]
    g = build_unit_graph(cat, ids, done, static.bundles, static.bundle_of)
    cal = Calendar(seasons=cat.seasons, start_year=2026, start_idx=cat.seasons.index(start), num_terms=num_terms)
    es, crit = compute_es(g, cat, cal, done)
    return cat, g, cal, es, crit


def edge(g, frm, to):
    for e in g.succs[g.unit_of[frm]]:
        if e.to == g.unit_of[to]:
            return e
    raise AssertionError("no edge: " + frm + " -> " + to)


class UnitGraphTest(unittest.TestCase):
    def test_bundle_becomes_one_unit(self):
        _, g, _, _, _ = build([course("Z1", 3, offered=["SPRING", "FALL"], coreqs=[["Z2"]]),
                               course("Z2", 1, offered=F, coreqs=[["Z1"]]),
                               course("N", prereqs=[["Z1"]])])
        u = g.unit_of["Z1"]
        self.assertEqual(g.unit_of["Z2"], u)
        self.assertEqual(g.units[u].id, "Z1+Z2")
        self.assertEqual(g.units[u].credits, 4)
        self.assertEqual(g.units[u].offered, ["FALL"])
        self.assertEqual(g.preds[u], [])  # coreq within a bundle is not an edge
        self.assertTrue(edge(g, "Z1", "N").hard)

    def test_completed_clause_has_no_edge(self):
        _, g, _, _, _ = build([course("A"), course("B"), course("C", prereqs=[["A", "B"]])], completed=["A"])
        self.assertEqual(g.preds[g.unit_of["C"]], [])

    def test_or_with_single_alternative_in_set_is_hard(self):
        _, g, _, _, _ = build([course("A"), course("B"), course("C", prereqs=[["A", "B"]])], ids=["A", "C"])
        e = edge(g, "A", "C")
        self.assertTrue(e.hard)
        self.assertEqual(e.alts, [g.unit_of["A"]])

    def test_topological_order(self):
        _, g, _, _, _ = build([course("C", prereqs=[["B"]]), course("B", prereqs=[["A"]]), course("A")])
        pos = {}
        for i, u in enumerate(g.order):
            pos[g.units[u].id] = i
        self.assertLess(pos["A"], pos["B"])
        self.assertLess(pos["B"], pos["C"])


class IsBindingTest(unittest.TestCase):
    def setUp(self):
        # C needs (A | B). A is available right away (ES 0), B comes after P (ES 1). D requires B via AND.
        self.cat, self.g, self.cal, self.es, _ = build([
            course("P"), course("A"), course("B", prereqs=[["P"]]),
            course("C", prereqs=[["A", "B"]]), course("D", prereqs=[["B"]]),
        ])

    def test_or_edge_from_earliest_alternative_binds(self):
        e = edge(self.g, "A", "C")
        self.assertFalse(e.hard)
        self.assertTrue(is_binding(e, self.es))

    def test_or_edge_from_later_alternative_does_not_bind(self):
        e = edge(self.g, "B", "C")
        self.assertFalse(e.hard)
        self.assertFalse(is_binding(e, self.es))

    def test_hard_edge_always_binds(self):
        e = edge(self.g, "B", "D")
        self.assertTrue(e.hard)
        self.assertTrue(is_binding(e, self.es))
        # hard always binds, regardless of ES
        es = list(self.es)
        es[self.g.unit_of["B"]] = 99
        self.assertTrue(is_binding(e, es))

    def test_tie_binds_all_tied_alternatives(self):
        _, g, _, es, _ = build([course("A"), course("B"), course("C", prereqs=[["A", "B"]])])
        self.assertTrue(is_binding(edge(g, "A", "C"), es))
        self.assertTrue(is_binding(edge(g, "B", "C"), es))

    def test_non_binding_edges_excluded_from_tail_and_descendants(self):
        tail = compute_tail(self.g, self.cal, self.es)
        desc = compute_descendants(self.g, self.es)
        u = self.g.unit_of
        self.assertEqual(desc[u["A"]], 1)       # C
        self.assertEqual(desc[u["B"]], 1)       # only D (the OR edge to C is excluded)
        self.assertEqual(desc[u["P"]], 2)       # B, D
        fall = self.cat.seasons.index("FALL")
        self.assertEqual(tail[u["A"]][fall], 1)
        self.assertEqual(tail[u["P"]][fall], 2)  # P -> B -> D


class TermMathTest(unittest.TestCase):
    def test_fall_only_chain_tail_counts_year_gap(self):
        cat, g, cal, es, _ = build([course("A", offered=F), course("B", offered=F, prereqs=[["A"]]),
                                    course("C", offered=S, prereqs=[["B"]])])
        u = g.unit_of
        self.assertEqual([es[u["A"]], es[u["B"]], es[u["C"]]], [0, 2, 3])
        tail = compute_tail(g, cal, es)
        fall = cat.seasons.index("FALL")
        self.assertEqual(tail[u["B"]][fall], 1)
        self.assertEqual(tail[u["A"]][fall], 3)

    def test_es_uses_earliest_or_alternative_and_coreq_delta_zero(self):
        _, g, _, es, crit = build([course("A"), course("B", prereqs=[["A"]]), course("K", coreqs=[["B"]]),
                                   course("C", prereqs=[["A", "B"]])])
        u = g.unit_of
        self.assertEqual(es[u["K"]], 1)   # coreq: same term as B
        self.assertEqual(crit[u["K"]], u["B"])
        self.assertEqual(es[u["C"]], 1)   # via A

    def test_ls_follows_hard_edges_and_offered(self):
        _, g, cal, _, _ = build([course("A"), course("B", offered=S, prereqs=[["A"]])], start="FALL", num_terms=4)
        ls = compute_ls(g, cal)
        # terms 0..3 = F, S, F, S. B must be placed by term 3 (S) at the latest, A by term 2
        self.assertEqual(ls[g.unit_of["B"]], 3)
        self.assertEqual(ls[g.unit_of["A"]], 2)


if __name__ == "__main__":
    unittest.main()
