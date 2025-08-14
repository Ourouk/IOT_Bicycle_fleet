#!/usr/bin/env python3
import argparse
import math
import random
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import serial  # optional: only needed if using --serial
except Exception:
    serial = None

# ----------------------------
# Constants and conversions
# ----------------------------
EARTH_R = 6371000.0  # meters (spherical mean)
NM_IN_METERS = 1852.0
KMH_TO_KT = 0.539956803
MPS_TO_KT = 1.943844492

# Approximate bicycle route: Liège -> Huy -> Namur -> Gembloux -> Wavre -> Brussels
ROUTE_WAYPOINTS = [
    # (lat, lon)
    (50.6326, 5.5797),   # Liège
    (50.5180, 5.2408),   # Huy
    (50.4674, 4.8718),   # Namur
    (50.5621, 4.6985),   # Gembloux
    (50.7179, 4.6110),   # Wavre
    (50.8466, 4.3528),   # Brussels (Grand-Place vicinity)
]

# ----------------------------
# Utility math
# ----------------------------
def deg2rad(d): return d * math.pi / 180.0
def rad2deg(r): return r * 180.0 / math.pi

def haversine_m(p1, p2):
    lat1, lon1 = map(deg2rad, p1)
    lat2, lon2 = map(deg2rad, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2.0)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_R * c

def initial_bearing_deg(p1, p2):
    lat1, lon1 = map(deg2rad, p1)
    lat2, lon2 = map(deg2rad, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    brng = math.atan2(x, y)
    brng = (rad2deg(brng) + 360.0) % 360.0
    return brng

def interpolate_on_geodesic(p1, p2, frac):
    # Slerp on a sphere (great-circle interpolation)
    lat1, lon1 = map(deg2rad, p1)
    lat2, lon2 = map(deg2rad, p2)
    d = 2.0 * math.asin(math.sqrt(
        math.sin((lat2 - lat1)/2.0)**2 +
        math.cos(lat1)*math.cos(lat2)*math.sin((lon2 - lon1)/2.0)**2
    ))
    if d == 0:
        return p1
    A = math.sin((1.0 - frac) * d) / math.sin(d)
    B = math.sin(frac * d) / math.sin(d)
    x = A*math.cos(lat1)*math.cos(lon1) + B*math.cos(lat2)*math.cos(lon2)
    y = A*math.cos(lat1)*math.sin(lon1) + B*math.cos(lat2)*math.sin(lon2)
    z = A*math.sin(lat1) + B*math.sin(lat2)
    lat = math.atan2(z, math.sqrt(x*x + y*y))
    lon = math.atan2(y, x)
    return (rad2deg(lat), (rad2deg(lon) + 540.0) % 360.0 - 180.0)  # normalize lon

# ----------------------------
# Route progress helper
# ----------------------------
class RouteCursor:
    def __init__(self, waypoints):
        self.wpts = waypoints
        self.legs = []
        for i in range(len(waypoints) - 1):
            d = haversine_m(waypoints[i], waypoints[i+1])
            self.legs.append(d)
        self.total_len = sum(self.legs)
        self.leg_idx = 0
        self.leg_progress_m = 0.0  # distance along current leg

    def done(self):
        return self.leg_idx >= len(self.legs)

    def position_and_course(self):
        if self.done():
            # Return final point and course 0
            return self.wpts[-1], 0.0
        p1 = self.wpts[self.leg_idx]
        p2 = self.wpts[self.leg_idx + 1]
        frac = min(1.0, max(0.0, self.leg_progress_m / max(1e-6, self.legs[self.leg_idx])))
        pos = interpolate_on_geodesic(p1, p2, frac)
        course = initial_bearing_deg(pos, p2)
        return pos, course

    def advance(self, step_m):
        while step_m > 0 and not self.done():
            leg_len = self.legs[self.leg_idx]
            remaining = leg_len - self.leg_progress_m
            if step_m < remaining:
                self.leg_progress_m += step_m
                step_m = 0.0
            else:
                step_m -= remaining
                self.leg_idx += 1
                self.leg_progress_m = 0.0

# ----------------------------
# Random speed model (OU process)
# ----------------------------
class OUSpeed:
    """
    Ornstein–Uhlenbeck speed model:
      dv = theta*(mu - v)*dt + sigma*sqrt(dt)*N(0,1)
    """
    def __init__(self, target_mps: float, var_pct: float = 5.0, theta: float = 0.6,
                 sigma_mult: float = 1.0, seed: int | None = None):
        self.mu = max(0.0, float(target_mps))
        self.theta = float(theta)
        var_frac = max(0.0, var_pct) / 100.0
        stationary_std = var_frac * self.mu
        self.sigma = sigma_mult * stationary_std * math.sqrt(2.0 * self.theta)
        self.v = self.mu
        self.rng = random.Random(seed)

    def step(self, dt: float) -> float:
        if dt <= 0:
            return max(0.0, self.v)
        dW = self.rng.gauss(0.0, 1.0) * math.sqrt(dt)
        dv = self.theta * (self.mu - self.v) * dt + self.sigma * dW
        self.v = max(0.0, self.v + dv)
        return self.v

# ----------------------------
# NMEA helpers
# ----------------------------
def nmea_checksum(sentence_wo_dollar: str) -> str:
    csum = 0
    for ch in sentence_wo_dollar:
        csum ^= ord(ch)
    return f"{csum:02X}"

def format_lat(lat):
    hemi = 'N' if lat >= 0 else 'S'
    lat = abs(lat)
    deg = int(lat)
    minutes = (lat - deg) * 60.0
    return f"{deg:02d}{minutes:07.4f}", hemi

def format_lon(lon):
    hemi = 'E' if lon >= 0 else 'W'
    lon = abs(lon)
    deg = int(lon)
    minutes = (lon - deg) * 60.0
    return f"{deg:03d}{minutes:07.4f}", hemi

def build_gprmc(dt_utc: datetime, lat, lon, sog_kt, cog_deg, status='A'):
    hhmmss = dt_utc.strftime("%H%M%S")
    ms = f"{dt_utc.microsecond/1e6:.3f}"[1:]  # .sss
    time_field = hhmmss + ms
    lat_str, lat_hemi = format_lat(lat)
    lon_str, lon_hemi = format_lon(lon)
    sog_str = f"{sog_kt:.1f}"
    cog_str = f"{cog_deg:.1f}"
    date_field = dt_utc.strftime("%d%m%y")
    # magnetic variation fields left empty
    fields = [
        "GPRMC",
        time_field,
        status,
        lat_str, lat_hemi,
        lon_str, lon_hemi,
        sog_str,
        cog_str,
        date_field,
        "",  # mag var
        ""   # mag E/W
    ]
    core = ",".join(fields)
    checksum = nmea_checksum(core)
    return f"${core}*{checksum}"

def build_gpgga(dt_utc: datetime, lat, lon, fix_quality=1, num_sats=10, hdop=0.9,
                alt_m=100.0, geoid_sep_m=46.0):
    hhmmss = dt_utc.strftime("%H%M%S")
    ms = f"{dt_utc.microsecond/1e6:.3f}"[1:]  # .sss
    time_field = hhmmss + ms
    lat_str, lat_hemi = format_lat(lat)
    lon_str, lon_hemi = format_lon(lon)
    fields = [
        "GPGGA",
        time_field,
        lat_str, lat_hemi,
        lon_str, lon_hemi,
        str(int(fix_quality)),
        f"{int(num_sats):02d}",
        f"{hdop:.1f}",
        f"{alt_m:.1f}", "M",
        f"{geoid_sep_m:.1f}", "M",
        "",  # age of DGPS
        ""   # DGPS ref ID
    ]
    core = ",".join(fields)
    checksum = nmea_checksum(core)
    return f"${core}*{checksum}"

# ----------------------------
# Main simulation
# ----------------------------
def parse_args():
    p = argparse.ArgumentParser(description="NMEA GPS simulator: Liège -> Brussels (bike).")
    p.add_argument("--speed-kmh", type=float, default=20.0, help="Target average speed (km/h).")
    p.add_argument("--speed-var-pct", type=float, default=5.0, help="Approx 1σ percent variation around target speed.")
    p.add_argument("--speed-theta", type=float, default=0.6, help="Mean-reversion strength (1/s).")
    p.add_argument("--speed-sigma-mult", type=float, default=1.0, help="Scale the volatility (1.0 = default).")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility.")
    p.add_argument("--rate-hz", type=float, default=1.0, help="Output update rate (Hz).")
    p.add_argument("--serial", type=str, default=None, help="Serial port to write to (e.g., /dev/ttyUSB0, COM5).")
    p.add_argument("--baud", type=int, default=9600, help="Serial baud rate (default 9600).")
    p.add_argument("--loop", action="store_true", help="Loop the route continuously.")
    p.add_argument("--sentences", type=str, default="RMC,GGA", help="Comma list of sentences to output (RMC,GGA,VTG).")
    p.add_argument("--start-time", type=str, default=None,
                   help="UTC start time ISO8601 (e.g., 2025-08-14T12:00:00Z). Default: now.")
    p.add_argument("--min-mult", type=float, default=0.2, help="Soft lower clamp as fraction of target speed.")
    p.add_argument("--max-mult", type=float, default=1.5, help="Soft upper clamp as fraction of target speed.")
    p.add_argument("--traffic-stops", action="store_true",
                   help="Simulate occasional short stops (e.g., traffic lights).")
    p.add_argument("--stop-prob-per-min", type=float, default=0.3, help="Stop start probability per minute.")
    p.add_argument("--stop-len-s", type=float, default=10.0, help="Stop duration in seconds.")
    return p.parse_args()

def main():
    args = parse_args()

    if args.serial and serial is None:
        print("pyserial is not installed. Install with: pip install pyserial", file=sys.stderr)
        sys.exit(1)

    if args.seed is not None:
        random.seed(args.seed)

    try:
        if args.start_time:
            st = args.start_time.rstrip("Z")
            start_utc = datetime.fromisoformat(st).replace(tzinfo=timezone.utc)
        else:
            start_utc = datetime.now(timezone.utc)
    except Exception:
        print("Invalid --start-time. Use e.g. 2025-08-14T12:00:00Z", file=sys.stderr)
        sys.exit(1)

    update_dt = 1.0 / max(0.1, args.rate_hz)
    target_speed_mps = (args.speed_kmh * 1000.0) / 3600.0
    speed_model = OUSpeed(target_mps=target_speed_mps,
                          var_pct=args.speed_var_pct,
                          theta=args.speed_theta,
                          sigma_mult=args.speed_sigma_mult,
                          seed=args.seed)

    cursor = RouteCursor(ROUTE_WAYPOINTS)

    # Traffic-light micro-stop state
    stop_timer = 0.0

    use_serial = False
    ser = None
    if args.serial:
        ser = serial.Serial(args.serial, args.baud, timeout=0)  # non-blocking
        use_serial = True
        print(f"Streaming NMEA to {args.serial} @ {args.baud} baud...", file=sys.stderr)

    t0 = time.perf_counter()
    sim_time = start_utc

    sentence_set = {s.strip().upper() for s in args.sentences.split(",") if s.strip()}
    if not sentence_set:
        sentence_set = {"RMC", "GGA"}

    try:
        while True:
            loop_start = time.perf_counter()

            # 1) Speed step with optional stops
            current_speed_mps = speed_model.step(update_dt)

            # Optional micro-stops
            if args.traffic_stops:
                if stop_timer > 0:
                    current_speed_mps = 0.0
                    stop_timer = max(0.0, stop_timer - update_dt)
                else:
                    # Poisson-ish trigger
                    if random.random() < (args.stop_prob_per_min / 60.0) * update_dt:
                        stop_timer = float(args.stop_len_s)
                        current_speed_mps = 0.0

            # Soft clamps
            current_speed_mps = max(args.min_mult * target_speed_mps,
                                    min(current_speed_mps, args.max_mult * target_speed_mps))

            # 2) Advance along route
            step_m = current_speed_mps * update_dt
            cursor.advance(step_m)
            pos, course = cursor.position_and_course()
            lat, lon = pos
            sog_kt = current_speed_mps * MPS_TO_KT

            # 3) Build sentences
            lines = []
            if "RMC" in sentence_set:
                lines.append(build_gprmc(sim_time, lat, lon, sog_kt, course))
            if "GGA" in sentence_set:
                lines.append(build_gpgga(sim_time, lat, lon))
            if "VTG" in sentence_set:
                # Optional VTG sentence
                cog_t = f"{course:.1f}"
                sog_kn = f"{sog_kt:.1f}"
                sog_kmh = f"{current_speed_mps * 3.6:.1f}"
                core = f"GPVTG,{cog_t},T,,M,{sog_kn},N,{sog_kmh},K"
                lines.append(f"${core}*{nmea_checksum(core)}")

            output = "\r\n".join(lines) + "\r\n"

            # 4) Output
            if use_serial:
                ser.write(output.encode("ascii", errors="ignore"))
            else:
                sys.stdout.write(output)
                sys.stdout.flush()

            # 5) Check completion
            if cursor.done():
                if args.loop:
                    cursor = RouteCursor(ROUTE_WAYPOINTS)
                    # keep time flowing
                else:
                    break

            # 6) Pace wall-clock to desired rate and advance sim time
            sim_time += timedelta(seconds=update_dt)
            elapsed = time.perf_counter() - loop_start
            sleep_s = max(0.0, update_dt - elapsed)
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        pass
    finally:
        if ser:
            ser.flush()
            ser.close()

if __name__ == "__main__":
    main()
