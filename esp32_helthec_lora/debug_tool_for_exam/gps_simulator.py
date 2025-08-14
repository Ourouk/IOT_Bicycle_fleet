#!/usr/bin/env python3
import math, time, sys, argparse, datetime, itertools
try:
    import serial  # only needed if using --serial
except Exception:
    serial = None

# Waypoints (lat, lon in decimal degrees) Liège → Huy → Namur → Wavre → Brussels
ROUTE = [
    (50.632557, 5.579666),  # Liège center
    (50.519993, 5.333333),  # Huy
    (50.466898, 4.867909),  # Namur
    (50.717899, 4.613022),  # Wavre
    (50.846557, 4.351697),  # Brussels (Grand-Place vicinity)
]

EARTH_R = 6371000.0  # meters
KMH_TO_KT = 0.539956803
MPS_TO_KT = 1.943844492

def haversine(p1, p2):
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return EARTH_R * c  # meters

def initial_bearing(p1, p2):
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360

def interpolate(p1, p2, frac):
    # Spherical linear interpolation (slerp) for geodesic between two points
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    delta = 2 * math.asin(math.sqrt(
        math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    ))
    if delta == 0:
        return p1
    A = math.sin((1-frac)*delta) / math.sin(delta)
    B = math.sin(frac*delta) / math.sin(delta)
    x = A*math.cos(lat1)*math.cos(lon1) + B*math.cos(lat2)*math.cos(lon2)
    y = A*math.cos(lat1)*math.sin(lon1) + B*math.cos(lat2)*math.sin(lon2)
    z = A*math.sin(lat1) + B*math.sin(lat2)
    lat = math.atan2(z, math.sqrt(x*x + y*y))
    lon = math.atan2(y, x)
    return (math.degrees(lat), math.degrees(lon))

def to_nmea_lat(lat):
    hemi = 'N' if lat >= 0 else 'S'
    lat = abs(lat)
    deg = int(lat)
    minutes = (lat - deg) * 60
    return f"{deg:02d}{minutes:07.4f}", hemi

def to_nmea_lon(lon):
    hemi = 'E' if lon >= 0 else 'W'
    lon = abs(lon)
    deg = int(lon)
    minutes = (lon - deg) * 60
    return f"{deg:03d}{minutes:07.4f}", hemi

def nmea_checksum(sentence_wo_dollar):
    c = 0
    for ch in sentence_wo_dollar:
        c ^= ord(ch)
    return f"{c:02X}"

def make_gprmc(dt_utc, lat, lon, sog_kt, cog_deg, magvar=None):
    # RMC: Recommended Minimum Specific GPS/Transit Data
    time_str = dt_utc.strftime("%H%M%S")
    date_str = dt_utc.strftime("%d%m%y")
    lat_str, lat_hemi = to_nmea_lat(lat)
    lon_str, lon_hemi = to_nmea_lon(lon)
    status = 'A'  # data valid
    sog = f"{sog_kt:.1f}"
    cog = f"{cog_deg:.1f}"
    mv = "" if magvar is None else f"{abs(magvar):.1f},{'E' if magvar >= 0 else 'W'}"
    core = f"GPRMC,{time_str}.00,{status},{lat_str},{lat_hemi},{lon_str},{lon_hemi},{sog},{cog},{date_str},{mv}"
    cs = nmea_checksum(core)
    return f"${core}*{cs}"

def make_gpgga(dt_utc, lat, lon, alt_m=100.0, nsat=10, hdop=0.9, geoid_sep=46.0):
    time_str = dt_utc.strftime("%H%M%S")
    lat_str, lat_hemi = to_nmea_lat(lat)
    lon_str, lon_hemi = to_nmea_lon(lon)
    fix_quality = 1  # GPS fix
    alt = f"{alt_m:.1f}"
    geoid = f"{geoid_sep:.1f}"
    core = f"GPGGA,{time_str}.00,{lat_str},{lat_hemi},{lon_str},{lon_hemi},{fix_quality},{nsat:02d},{hdop:.1f},{alt},M,{geoid},M,,"
    cs = nmea_checksum(core)
    return f"${core}*{cs}"

def build_segments(route):
    segs = []
    for a, b in zip(route, route[1:]):
        dist = haversine(a, b)
        brg = initial_bearing(a, b)
        segs.append({"start": a, "end": b, "dist_m": dist, "bearing": brg})
    return segs

def stream(route, start_dt_utc, speed_kmh=18.0, hz=1, altitude_profile=(120, 100)):
    segs = build_segments(route)
    total_dist = sum(s["dist_m"] for s in segs)
    # simple linear altitude from first to last waypoint
    alt_start, alt_end = altitude_profile
    dt = 1.0 / hz
    speed_mps = speed_kmh / 3.6
    sog_kt = speed_mps * MPS_TO_KT

    # iterate along whole path by distance
    s_travel = 0.0
    t = 0
    # precompute cumulative segment ends
    cum = list(itertools.accumulate(s["dist_m"] for s in segs))
    seg_idx = 0

    while seg_idx < len(segs):
        # how far along whole route
        frac_route = min(s_travel / total_dist, 1.0)
        # find current segment
        while seg_idx < len(segs) and s_travel > (cum[seg_idx] if seg_idx < len(cum) else total_dist):
            seg_idx += 1
        if seg_idx >= len(segs):
            break
        seg = segs[seg_idx]
        seg_start_s = cum[seg_idx-1] if seg_idx > 0 else 0.0
        seg_frac = max(0.0, min((s_travel - seg_start_s) / seg["dist_m"], 1.0))
        lat, lon = interpolate(seg["start"], seg["end"], seg_frac)
        alt = alt_start + (alt_end - alt_start) * frac_route
        cog = seg["bearing"]

        now = start_dt_utc + datetime.timedelta(seconds=t)
        yield now, lat, lon, sog_kt, cog, alt

        t += int(1 / hz)
        s_travel += speed_mps * dt

def main():
    ap = argparse.ArgumentParser(description="NMEA GPS simulator Liège→Brussels (bike)")
    ap.add_argument("--speed-kmh", type=float, default=18.0, help="average speed in km/h (default 18)")
    ap.add_argument("--hz", type=float, default=1.0, help="update rate Hz (default 1)")
    ap.add_argument("--start", type=str, default=None, help="UTC start time ISO8601 (e.g., 2025-08-14T06:00:00Z). Default: now UTC")
    ap.add_argument("--serial", type=str, default=None, help="serial port (e.g., /dev/ttyUSB0 or COM5). If omitted, prints to stdout")
    ap.add_argument("--baud", type=int, default=9600, help="baud rate (default 4800)")
    ap.add_argument("--route", type=str, default=None, help="optional CSV of lat,lon; overrides built-in route")
    args = ap.parse_args()

    route = ROUTE
    if args.route:
        parsed = []
        for tok in args.route.split(";"):
            latlon = tok.strip().split(",")
            if len(latlon) != 2:
                raise ValueError("Route must be 'lat,lon;lat,lon;...'")
            parsed.append((float(latlon[0]), float(latlon[1])))
        if len(parsed) < 2:
            raise ValueError("Route needs at least two points")
        route = parsed

    if args.start:
        # expect ...Z or no timezone treated as UTC
        s = args.start.replace("Z","")
        start_dt_utc = datetime.datetime.fromisoformat(s)
    else:
        start_dt_utc = datetime.datetime.utcnow().replace(microsecond=0)

    out = None
    if args.serial:
        if serial is None:
            print("pyserial not installed. Run: pip install pyserial", file=sys.stderr)
            sys.exit(1)
        out = serial.Serial(args.serial, args.baud, bytesize=8, parity='N', stopbits=1)
    else:
        out = sys.stdout

    try:
        for now, lat, lon, sog_kt, cog, alt in stream(route, start_dt_utc, args.speed_kmh, args.hz):
            rmc = make_gprmc(now, lat, lon, sog_kt, cog)
            gga = make_gpgga(now, lat, lon, alt_m=alt)
            line = rmc + "\r\n" + gga + "\r\n"
            if hasattr(out, "write") and out is sys.stdout:
                out.write(line)
                out.flush()
            else:
                out.write(line.encode("ascii"))
                out.flush()
            time.sleep(1.0 / args.hz)
    except KeyboardInterrupt:
        pass
    finally:
        if out is not sys.stdout and out is not None:
            out.close()

if __name__ == "__main__":
    main()
