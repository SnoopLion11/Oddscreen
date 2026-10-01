"""Verification of the worst-case devig + EV% math for the ESX odds screen."""
import math, random
import shin as shinlib

def implied(american):
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)

def mult(p):
    S = sum(p); return [x / S for x in p]

def additive(p):
    S = sum(p); n = len(p); return [x - (S - 1) / n for x in p]

def power(p):
    lo, hi = 1e-6, 50.0
    for _ in range(200):
        k = (lo + hi) / 2
        if sum(x ** k for x in p) > 1: lo = k
        else: hi = k
    return [x ** k for x in p]

def shin_m(p):
    S = sum(p)
    f = lambda z: [(math.sqrt(z*z + 4*(1-z)*x*x/S) - z) / (2*(1-z)) for x in p]
    lo, hi = 0.0, 0.999
    for _ in range(200):
        z = (lo + hi) / 2
        if sum(f(z)) > 1: lo = z
        else: hi = z
    return f(z)

def worst(p, i):
    vals = {"multiplicative": mult(p)[i], "additive": additive(p)[i], "power": power(p)[i], "shin": shin_m(p)[i]}
    m = min(vals, key=vals.get)
    return vals[m], m, vals

def ev_pct(fair, cost):
    return (fair / cost - 1) * 100

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

# 1. Screenshot case: -110/-110, final +125 -> fair 50%, EV 12.5%
p = [implied(-110), implied(-110)]
for fn in (mult, additive, power, shin_m):
    check(f"screenshot {fn.__name__} fair=50%", abs(fn(p)[0] - 0.5) < 1e-9)
cost_125 = 1 / (1 + 125/100)  # price equivalent of +125
check("screenshot EV = 12.5%", abs(ev_pct(0.5, cost_125) - 12.5) < 1e-9)
# CNO formula: ((wager*payout_mult)*hit - wager)/wager
check("matches CNO EV formula", abs(((1*2.25)*0.5 - 1)/1*100 - ev_pct(0.5, cost_125)) < 1e-9)

# 2. Every method sums to 1, across random 2-way and 3-way markets
random.seed(1)
ok_sum = True; ok_shin_add = True; ok_lib = True; lib_skipped = 0
for _ in range(5000):
    n = random.choice([2, 3])
    q = [random.random() + 0.05 for _ in range(n)]; t = sum(q); q = [x/t for x in q]
    margin = random.uniform(0.0, 0.08)
    p = [x * (1 + margin) for x in q]
    if max(p) >= 0.99: continue
    for fn in (mult, additive, power, shin_m):
        if abs(sum(fn(p)) - 1) > 1e-9: ok_sum = False
    if n == 2 and max(abs(a - b) for a, b in zip(shin_m(p), additive(p))) > 1e-9: ok_shin_add = False
    r = shinlib.calculate_implied_probabilities([1/x for x in p], full_output=True)
    if r.delta > 1e-9: lib_skipped += 1; continue  # library hit its iteration cap without converging
    if max(abs(a - b) for a, b in zip(shin_m(p), r.implied_probabilities)) > 1e-6: ok_lib = False
check("all four methods sum to 100% (5,000 random markets)", ok_sum)
check("Shin == Additive on every 2-way market", ok_shin_add)
check(f"our Shin matches independent 'shin' library (2-way and 3-way; {lib_skipped} cases skipped where the library itself did not converge)", ok_lib)

# 3. Numbers quoted in the chat: -300 / +240
p = [implied(-300), implied(240)]
w_fav, m_fav, _ = worst(p, 0); w_dog, m_dog, _ = worst(p, 1)
check(f"fav worst = multiplicative 71.8% (got {m_fav} {w_fav:.4f})", m_fav == "multiplicative" and round(w_fav, 3) == 0.718)
check(f"dog worst = power 26.7% (got {m_dog} {w_dog:.4f})", m_dog == "power" and round(w_dog, 3) == 0.267)
check(f"ESX +300 with 1c fee: worst-case EV +2.7% (got {ev_pct(w_dog, 0.26):.2f})", round(ev_pct(w_dog, 0.26), 1) == 2.7)
check(f"ESX +300 with 1c fee: multiplicative EV +8.3% (got {ev_pct(mult(p)[1], 0.26):.2f})", round(ev_pct(mult(p)[1], 0.26), 1) == 8.3)

# 4. Worst case is never above any single method
ok = True
for _ in range(5000):
    q = [random.random() + 0.05 for _ in range(2)]; t = sum(q); q = [x/t for x in q]
    p = [x * (1 + random.uniform(0, 0.08)) for x in q]
    for i in range(2):
        w, _, vals = worst(p, i)
        if any(w > v + 1e-12 for v in vals.values()): ok = False
check("worst case <= every method, every side", ok)

# 5. Edge case: additive goes negative on extreme longshots
p = [0.88, 0.18, 0.015]  # 3-way market with a deep longshot and a wide margin
a = additive(p)[2]
check(f"additive can go negative on a deep longshot (got {a:.4f}) - code must guard this", a < 0)

print("\nFAILURES:", fails)
