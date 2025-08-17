#!/usr/bin/env sh

docker exec -it mongodb mongosh -u administrator -p Administrator --authenticationDatabase admin hepl --eval 'db.logs.find().sort({ts:-1}).limit(5).pretty()'

