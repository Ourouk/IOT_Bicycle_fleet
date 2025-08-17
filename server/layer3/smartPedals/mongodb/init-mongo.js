// Use (or create) the "hepl" DB
db = db.getSiblingDB('hepl');

// Collection "logs" (1 document = 1 station at a timestamp)
db.createCollection('logs');

// TTL: auto-delete after 30 days based on Date field "ts"
db.logs.createIndex({ ts: 1 }, { expireAfterSeconds: 60 * 60 * 24 * 30 });

// Query index: by station and newest first
db.logs.createIndex({ station_id: 1, ts: -1 });
