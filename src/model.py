"""Term-structure model: Dupoyet-Daigler-Chen closed-form VIX futures pricing.

    F(V_t, dt) = V_t * A^dt + B * (1 - A^dt)

with A = e^{-b} in (0,1), B = (a + mu*lambda)/b >= 0, dt in days to maturity.

Daily (re-)estimation by nonlinear least squares over the cross-section of
spot VIX + listed futures settles, following the thesis (t = # days in the
estimation window; the thesis primary spec is t=1).
"""
import numpy as np
from scipy.optimize import least_squares

A_LO, A_HI = 1e-4, 1.0 - 1e-9
B_LO, B_HI = 1e-2, 300.0


def price(spot, A, B, dtm):
    """Model futures price given spot, params, days-to-maturity (array-safe)."""
    dtm = np.asarray(dtm, dtype=float)
    return spot * np.power(A, dtm) + B * (1.0 - np.power(A, dtm))


def forecast_next_day(spot_t, A, B, dtm_t):
    """Thesis forecast: today's params, one day of time decay.

    F~_{t+1} = V_t * A^(dtm-1) + B * (1 - A^(dtm-1))
    """
    return price(spot_t, A, B, np.asarray(dtm_t, dtype=float) - 1.0)


def fit_params(day_frames, t=1):
    """Fit (A, B) by NLS.

    day_frames: list of DataFrames, most-recent last, each with columns
        ['spot', 'settle', 'dtm'] for the contracts listed that day
        (spot row should have dtm == 0).
    t is informational here (len(day_frames) should equal t).

    Returns (A, B) or None if the fit cannot be performed.
    """
    spots, settles, dtms = [], [], []
    for i, df in enumerate(reversed(day_frames)):  # i=0 -> most recent day
        df = df[(df["settle"] > 0) & np.isfinite(df["settle"])]
        if df.empty:
            continue
        s = float(df["spot"].iloc[0])
        if not np.isfinite(s) or s <= 0:
            continue
        spots.append(s)
        settles.append(df["settle"].to_numpy(dtype=float))
        dtms.append(df["dtm"].to_numpy(dtype=float) + i)  # +i days older
    if not spots:
        return None
    # build residual vector
    S = np.repeat(np.array(spots, dtype=float),
                  [len(x) for x in settles])
    Y = np.concatenate(settles)
    D = np.concatenate(dtms)
    if Y.size < 2:  # need at least as many points as params
        return None

    def resid(p):
        A, B = p
        return price(S, A, B, D) - Y

    try:
        sol = least_squares(resid, x0=np.array([0.99, 20.0]),
                            bounds=([A_LO, B_LO], [A_HI, B_HI]),
                            method="trf", max_nfev=2000,
                            xtol=1e-10, ftol=1e-10, gtol=1e-10)
    except Exception:  # noqa: BLE001
        return None
    if not sol.success:
        return None
    A, B = float(sol.x[0]), float(sol.x[1])
    if not (A_LO < A < A_HI and B_LO <= B <= B_HI):
        return None
    return A, B
