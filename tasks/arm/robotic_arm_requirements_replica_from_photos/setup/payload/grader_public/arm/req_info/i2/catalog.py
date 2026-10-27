"""A catalogue as DATA (public, number-free code): the grader loads the truth (its GT folder's catalogue.json, the generator-side
truth of the datasheet pages and the quote); the practice checker loads the agent's own transcription of the pages
(schemas.catalogue_schema). Both have the format req-i2-catalogue/1, SI units:

  servos: [{part_id, vendor, body_class, protocol, voltage_range_v [lo, hi], points [{voltage_v, stall_torque_nm,
            no_load_speed_rad_s, stall_current_a}] (one or two voltage points), no_load_current_a, rated_torque_nm,
            output_inertia_kgm2, mass_kg, price_usd}]
  boards: [{part_id, protocols [..], input_range_v [lo, hi], price_usd}]
  psus:   [{part_id, output_v, rated_current_a, price_usd}]
  vendors: {vendor: {inspected: bool}}        (a vendor without a lot report is not inspected: brief B-10)
Order of the lists = the quote's order (ties of the repair and the optimum search are broken by price, then by this order).
"""
import copy
import json


class Catalogue:
    def __init__(self, data):
        if isinstance(data, str):
            with open(data) as fh:
                data = json.load(fh)
        self.data = copy.deepcopy(data)
        self.servos = list(self.data.get("servos", []))
        self.boards = list(self.data.get("boards", []))
        self.psus = list(self.data.get("psus", []))
        self.vendors = dict(self.data.get("vendors", {}))
        self._by = {}
        for role, items in (("servo", self.servos), ("bus_board", self.boards), ("psu", self.psus)):
            for it in items:
                d = dict(it)
                d["role"] = role
                self._by[it["part_id"]] = d
        self.order = [x["part_id"] for x in self.servos + self.boards + self.psus]
        self._si = {}

    def has(self, pid):
        return pid in self._by

    def get(self, pid):
        return self._by[pid]

    def role(self, pid):
        return self._by[pid]["role"]

    def price(self, pid):
        return float(self._by[pid]["price_usd"])

    def index(self, pid):
        return self.order.index(pid)

    def inspected(self, vendor):
        return bool(self.vendors.get(vendor, {}).get("inspected", False))

    def si(self, pid):
        if pid not in self._si:
            self._si[pid] = self._si_uncached(pid)
        return self._si[pid]

    def _si_uncached(self, pid):
        s = self._by[pid]
        pts = sorted((float(p["voltage_v"]), float(p["stall_torque_nm"]), float(p["no_load_speed_rad_s"]),
                      float(p["stall_current_a"])) for p in s["points"])
        return {"part_id": pid, "vendor": s["vendor"], "points": pts, "rated_nm": float(s["rated_torque_nm"]),
                "J_out": float(s["output_inertia_kgm2"]), "mass_kg": float(s["mass_kg"]), "i0": float(s["no_load_current_a"]),
                "vrange": tuple(float(x) for x in s["voltage_range_v"]), "body_class": s["body_class"],
                "protocol": s["protocol"], "price": float(s["price_usd"]), "inspected": self.inspected(s["vendor"])}

    @staticmethod
    def nominal_at(sku_si, v):
        """Stall torque, no-load speed, stall current at the servo voltage v: linear interpolation (and extrapolation)
        between two voltage points; one point: in proportion to the voltage (brief B-14 e)."""
        pts = sku_si["points"]
        if len(pts) == 1:
            v1, t1, w1, i1 = pts[0]
            k = float(v) / v1
            return t1 * k, w1 * k, i1 * k
        (va, ta, wa, ia), (vb, tb, wb, ib) = pts[0], pts[-1]
        a = (float(v) - va) / (vb - va)
        return ta + a * (tb - ta), wa + a * (wb - wa), ia + a * (ib - ia)

    def servo_ok_on_bus(self, pid, bus_v):
        lo, hi = self.si(pid)["vrange"]
        return lo - 1e-9 <= float(bus_v) <= hi + 1e-9

    def boards_cover(self, board_ids, protocols, bus_v):
        cov = set()
        for bid in board_ids:
            if bid not in self._by or self._by[bid]["role"] != "bus_board":
                continue
            b = self._by[bid]
            if b["input_range_v"][0] - 1e-9 <= bus_v <= b["input_range_v"][1] + 1e-9:
                cov |= set(b["protocols"])
        return set(protocols) <= cov

    def cheapest_board_cover(self, protocols, bus_v):
        need = set(protocols)
        ok = [b for b in self.boards if b["input_range_v"][0] - 1e-9 <= bus_v <= b["input_range_v"][1] + 1e-9]
        best = None
        n = len(ok)
        for mask in range(1, 1 << n):
            chosen = [ok[i] for i in range(n) if mask >> i & 1]
            cov = set()
            for b in chosen:
                cov |= set(b["protocols"])
            if not need <= cov:
                continue
            cost = round(sum(float(b["price_usd"]) for b in chosen), 2)
            key = (cost, [self.boards.index(b) for b in chosen])
            if best is None or key < best[0]:
                best = (key, [b["part_id"] for b in chosen])
        return None if best is None else (best[0][0], best[1])

    def bom_cost(self, bom):
        c = sum(self.price(s) for s in bom["servos"].values() if self.has(s))
        c += sum(self.price(b) for b in bom.get("boards", []) if self.has(b))
        if bom.get("psu") and self.has(bom["psu"]):
            c += self.price(bom["psu"])
        return round(c, 2)
