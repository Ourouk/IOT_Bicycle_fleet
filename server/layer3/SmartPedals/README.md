# VPN ZeroTier
## **VM:** Download the repo + zerotier
```bash
curl -s https://install.zerotier.com | sudo bash
```
## **Web:** Create a network in ZeroTier Central
- _Create a Network_
- Copy the _ID_
- Activate _Private Network_
- Select _Auto Assign IPs_
## **VM:** Join a network with the id
```bash
sudo zerotier-cli join <id>
```
## **Web:** Authorize your device in ZeroTier Central
- Extend _Members_ panel
- Select a connection and click on _Edit_
	- Check _Authorized_ and name the connection (you can retreive the vm MAC with _sudo zerotier-cli listnetworks_ or the Address label with _sudo zerotier-cli info_)
