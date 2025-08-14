// Create or use smartpedals db
db = db.getSiblingDB('smartpedals');

// Create user that have rights
db.createUser({
  user: 'smartuser',
  pwd: 'smartpass',
  roles: [
    {
      role: 'readWrite',
      db: 'smartpedals'
    }
  ]
});

// Create users collection
db.createCollection('users');
db.users.createIndex({ rfid: 1 }, { unique: true });
db.users.insertMany([
  {
    firstName: "Z",
    lastName: "Zorglub",
    email: "z.zorglub@example.com",
    phone: "0123456789",
    rfid: "rfid123",
    history:   []
  },
  {
    firstName: "Ilkor",
    lastName: "Olrik",
    email: "I.Olrik@example.com",
    phone: "0987654321",
    rfid: "rfid456",
    history: [
    {
      bikeId: "bike002",
      action: "undock",
      timestamp: new Date("2025-08-06T10:00:00+02:00")
    }
    ]
  }
]);

// Create bikes colletion
db.createCollection('bikes');
db.bikes.createIndex({ bikeId: 1 }, { unique: true });
db.bikes.insertMany([
  {
    bikeId: "bike001",
    status: "available",
    currentUser: null,
    currentRack: "rack001",
    history: []
  },
  {
    bikeId: "bike002",
    status: "in_use",
    currentUser: "rfid456",
    currentRack: null,
    history: [
      {
        action: "undock",
        userRfid: "rfid456",
        timestamp: new Date("2025-08-06T10:00:00+02:00")
      }
    ]
  }
]);

// Create racks collection
db.createCollection('racks');
db.racks.createIndex({ rackId: 1 }, { unique: true });
db.racks.insertMany([
  {
    rackId: "rack001",
    stationId: "station001",
    currentBike: "bike001",
    history: [
      {
        bikeId: "bike001",
        action: "dock",
        timestamp: new Date("2025-08-06T08:00:00+02:00")
      }
    ]
  },
  {
    rackId: "rack002",
    stationId: "station001",
    currentBike: null,
    history: [
      {
        bikeId: "bike002",
        action: "dock",
        timestamp: new Date("2025-08-06T07:00:00+02:00")
      },
      {
        bikeId: "bike002",
        action: "undock",
        timestamp: new Date("2025-08-06T10:00:00+02:00")
      }
    ]
  }
]);

// Create stations collection
db.createCollection('stations');
db.stations.createIndex({ stationId: 1 }, { unique: true });

db.stations.insertMany([
  {
    stationId: "station001",
    name: "Parking Gloesener",
    racks: ["rack001", "rack002"]
  },
  {
    stationId: "station002",
    name: "Parking Seraing",
    racks: []
  }
]);

// Create locations collection (7 days TTL)
db.createCollection('locations');
db.locations.createIndex({ timestamp: 1 }, { expireAfterSeconds: 7 * 24 * 60 * 60 });
db.locations.insertMany([
  // bike002 11 locations
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:00:00+02:00"),
                        satellites: 3,
                        coordinates: { lat: 50.619513294351876, lon: 5.582300030791969 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:05:00+02:00"),
                        satellites: 4,
                        coordinates: { lat: 50.619630889095625, lon: 5.5815224782220785 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:10:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.620185626777335, lon: 5.581437099275686 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:20:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.62094864399103, lon: 5.581283269436659 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:25:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.62111258535128, lon: 5.581952422333352 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:30:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.620745216774075, lon: 5.582711193921592 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:35:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.62039679835147, lon: 5.58375674532269 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:40:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.62013556180707, lon: 5.585041279901195 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:45:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.619120295037774, lon: 5.585172720648761 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:50:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.6187908182484, lon: 5.585029330742333 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T10:55:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.618935280391035, lon: 5.584028782302752 }
  },
  {
    bikeId: "bike002",
    type: "location",
    timestamp: new Date("2025-08-13T11:00:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.619449894749565, lon: 5.5828299365071565 }
  },

  // bike003 3 locations
  {
    bikeId: "bike003",
    type: "location",
    timestamp: new Date("2025-08-13T08:00:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.60879041600449, lon: 5.606638101882968 }
  },
  {
    bikeId: "bike003",
    type: "location",
    timestamp: new Date("2025-08-13T08:05:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.60657787346407, lon: 5.6071143390872 }
  },
  {
    bikeId: "bike003",
    type: "location",
    timestamp: new Date("2025-08-13T08:10:00+02:00"),
                        satellites: 5,
                        coordinates: { lat: 50.605270886046014, lon: 5.608532697716706 }
  }
]);
