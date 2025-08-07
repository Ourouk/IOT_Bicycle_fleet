apt-get update -y
apt-get install -y hostapd dnsmasq mosquitto
# Copy files from the folder to root
cp -r ./root/ /
systemctl unmask hostapd
systemctl enable --now hostapd
systemctl enable --now dnsmasq
systemctl enable --now mosquitto
# Enable the service to node-red
systemctl enable --now nodered