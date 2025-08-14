#!/usr/bin/env sh

docker exec -ti mongodb mongosh --tls --tlsCAFile /etc/ssl/ca.crt --tlsCertificateKeyFile /etc/ssl/mongodb.pem -u smartuser -p smartpass --authenticationDatabase smartpedals --tlsAllowInvalidHostnames true
