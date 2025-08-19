#import "template.typ": *
#import "@preview/tablem:0.3.0": tablem, three-line-table

#show: project.with(
  course-title: "IOT",
  date: datetime.today(),
  title: "Gestion de flottes de \n vélos connectés",
  authors: (
    (
      first-name: "Andrea",
      last-name: "Spelgatti",
      cursus: "M. Ing. Ind. - Informatique",
    ),
    (
      first-name: "\nMartin",
      last-name: "Van den Bossche",
      cursus: "M. Ing. Ind. - Informatique",
    ),
  ),
  bibliography-file: "ref.bib"
)

= Introduction
== Problématique
Lors de nos diverses balades en ville, nous pouvons désormais constater l'apparition de plus en plus fréquente de vélos en libre-service.
C'est ainsi que l'idée de gérer une flotte de vélos universitaires, disponibles pour les étudiants et le personnel sur un campus, nous est venue à l'esprit.
Des problématiques apparaissent directement avec cette idée, comme l'exemple du campus de Google à Mountain View, où une succession de vols des vélos mis à disposition sur le site a eu lieu, avec près de 250 disparitions par semaine #cite(<ruggieroSecurityFlawGoogle2018>).

On ne sait également pas qui utilise les vélos, si les règles d'utilisation sont respectées ou si les vélos sont en bon état.

Il semble donc nécessaire de mettre en place un système de gestion de ces vélos pour éviter ce genre de désagrément. Cet ajout est aussi l'occasion de proposer une série de fonctionnalités qui pourraient être utiles pour les utilisateurs.

== Objectif
L'objectif de ce projet est de réaliser un système de gestion permettant de suivre l'activité des vélos en libre-service à l'intérieur d'un campus.

Ce système devra permettre de gérer les vélos, les stations, les utilisateurs et les trajets.
Nous ajouterons par ailleurs quelques fonctionnalités intelligentes pour améliorer le confort de l'utilisateur.

== Proposition


Le projet consiste à réaliser trois objets connectés avec des capteurs différents. Ces objets sont les suivants :
- Un vélo connecté avec un ESP32 compatible LORA, intégrant une gestion des lumières en fonction de la luminosité, un buzzer, un GPS et un lecteur RFID pour prévenir le vol;
- Une station de sécurité/charge connectée avec un ESP32 compatible WIFI, comprenant une détection de la présence ou non d'un vélo et un système de déverrouillage par badge;
- Une antenne connectée avec un edge processing basé sur une Raspberry Pi se connectant aux deux autres objets et agissant comme un point relais ou un hub.

Le serveur de gestion sera réalisé en Python avec une base de données MongoDB.
Un serveur sera présent côté client de notre produit, et un second sera géré de notre côté pour la distribution de mises à jour, le dépannage et la télémétrie.

Les deux serveurs seraient basés sur Rocky Linux en utilisant une architecture containerisée avec Docker.

// == Diagramme
// #set page(flipped: true, margin: 2.5%)
// #figure(image("figures/IOT.jpg",width: 100%),)

// #set page(flipped: false,
//     margin:  (x: 3cm, top: 2cm, bottom: 2cm),header-ascent: 35%,
//   )
// = Layer 1
// == Matériel au dessus de la station (Raspberry PI)

// === Wifi Access Point
// Objectif:
// - Connection avec les ESP32
// ==== Configuration as Wifi AP
// Liste des outils utilisés :
// - hostpad - utilitaire permettant d'utiliser la rpi comme un access point
// - dnsmasq - simple serveur dhcp
// - interface - mettre les addresses ip fixes

// Les modification apportées à raspbian sont disponibles sur le git du projet /raspberry/root/.
// === NodeRed
// - Node crypto-blue pour gérer le déchiffrement des packets lora.
// - Node mqtt -> Communication L2/Esp32 WIFI
// - Node Serial -> Lecture/écriture sur le dragino la66 usb lora
// - Node fonction pour le parsing
// - Node GrovePi : Communication avec les différent capteurs 
// - Node 
// Les flows sont disponnibles sur le github /raspberry/root/home/pi/.nodered
// === Mosquitto
// - Instance publique sans sécurité spécifique pour gérer les communication avec l'esp32

// == Matériel Embarqué (Helltech Lora ESP32)
// #figure(
//     grid(
//         columns: 2,
//         gutter: 2mm,
//         image("hetecLoraV3_back2.png", width: 70%),
//         image("heltecLoraV3_IO.png", width: 100%)
//     )
// )
// == Communication LoRa RaspberryPI - Heltec Lora V3
// Pour limiter la taille des packets, nous utilisons un petit protocole simpliste similaire à du csv.

// Le tout crypter avec du AES-ECB (Nous connaissons le fait que ce chiffrement n'est plus recommander mais nous l'avons tout de même utilisé )
// == Matériel sur station (ESP32 Wifi)




// === Filaire jusqu'au serveur
// = Layer 1 to 2
// Pour gérer les communications entre les objets embarqués et les serveurs, nous utilisons le protocol mqtt avec plusieurs topic.
// == Encodage

// - Pour simplifier la lisibilité et le traitement des informations encodées, les antennes RPI communique avec le serveur L2 en utilisant du json
//   - Example bike communication
//   ```json
//   {
//     "bike_id": 1,
//     "type": "location",
//     "timestamp": "2023-10-01T12:00:00Z",
//     "satellites":3,
//     "coordinates": {
//       "lat": 50.8503,
//       "lon": 4.3517
//     },
//   }
//   ```
//   - Example station communication
// - Auth request
// ```json
//   {
//     "bike_id": 1,
//     "rack_id": 1,
//     "station_id": 1,
//     "type": "auth",
//     "action": "unlock",
//     "user_id": "andrea98",
//     "timestamp": "2023-10-01T12:00:00Z",
//   }
//   {
//     "bike_id": 1,
//     "rack_id": 1,
//     "station_id": 1,
//     "type": "auth",
//     "action": "lock",
//     "user_id": "andrea98",
//     "timestamp": "2023-10-01T12:00:00Z",
//   }
// ```
// - Reply
// ```json
//   {
//     "bike_id": 1,
//     "type": "auth_reply",
//     "action": "accept"
//     "user_id": "andrea98"
//     "timestamp": "2023-10-01T12:00:00Z",
//   }
//   {
//     "bike_id": 1,
//     "type": "auth_reply",
//     "action": "deny"
//     "user_id": "andrea98"
//     "timestamp": "2023-10-01T12:00:00Z",
//   }
// ```
// = Layer 2
// == Rocky Linux
// === Docker CE
// === Zerotier
// == Containers
// === Mongodb
// ! A l'intégrité !
// ==== Table Users
// ==== Table Bikes
// ==== Table Stations
 
// = Layer 3
// == Rocky Linux
// === Replication of Layer 2
// == Containers
// === NodeRed

// = Layer 4
// == WebEx
// == Twilio
// == Shodan
// == Maps Provider
// == ZeroTier
// == Broker public MQTT

= Prix
== Prix de fabrication
Selon le site de grovePI dexterIndustries.com, le kit coute 150 \$

Ce qui est hors de prix.
+ Raspberry Pi Model 4B 4Gb - 75 \$
+ Dragino LoRa Adaptator - 30\$

Par vélo 
+ Le heltec - 20 \$
+ Esp32 - 5 \$ 
+ Adafruit GPS V3 (we have v2) - 30\$

Pour un total  de 310 \$
== Estimation d'un prix plus raisonnable
Probablement en utilisant un custom pcb, et des esp32 pas de dev. ~2\$ + 0.5\$ 
Sur aliexpress on retrouve, des antennes Lora pour 5\$
Les capteurs simple type led etc pour dans les alentours de 1 \$

En utilisant des simples esp32 avec le module lora externe. Permettrait de réduire le prix à plus ou +-45\$ le vélo.

Les stations avec la même logique vers 20\$ la station mais en prenant compte du casing etc probablement + dans les 75 voir 100 \$

L'antenne Lora de base ne nécessite probablement pas une raspberryPi mais une alternative moins chère. Type Orange Pi Zero 15\$

Donc en choisissant des composant moins "user friendly" on peut diminuer les coups mais le coups par vélo reste élevé.

= Clientelles
Dans notre exemple initialle, nous parlons des vélos google victimes de nombreux vols car en libre services.

Donc nous sommes partits sur une clientelle de type universitaires/institutionnelles(villes)/grosses entreprises.
= Consommation électrique
== Général 
#tablem[
  | Appareil | Courant moyen | Puissance (≈5 V) | Énergie/jour | Remarques clés |
|---|---:|---:|---:|---|
| Heltec ESP32 LoRa v3  | 140–150 mA | 0,70–0,75 W | 0,017–0,018 kWh | GPS et base ESP32 dominent |
| ESP32‑WROOM | ~150 mA | ~0,75 W | ~0,018 kWh | Wi‑Fi dominent |
| Raspberry Pi passerelle  | 740–960 mA | 3,7–4,8 W | 0,089–0,115 kWh | Pi 4B ≈4,5–4,8 W; ventilateur ~1 W |
]
== RPI
#tablem[
| Sous-ensemble | Courant |
|---|---:|
| Carte ESP32 (base) | ~110 mA |
| LoRa en écoute | +5 mA |
| LoRa émission (0,1 s/30 s) | +0,4 mA (moyenne) |
| GPS Adafruit | +22 mA |
| Capteur de lumière | +1 mA |
| OLED embarqué | +2 mA |
| LED (occasionnelle) | +0,2 mA |
| Relais (ex. 10 % du temps) | +7 mA |
| Total arrondi | 140–150 mA |
]
== ESP32‑WROOM
#tablem[
  | Sous-ensemble | Courant |
|---|---:|
| ESP32 (Wi‑Fi perpétuel) | ~120 mA |
| RFID (MFRC522) | +20 mA |
| LED (1 ON + 1 occasionnelle) | +2,2 mA |
| Relais (ex. 10 % du temps) | +7 mA |
| Ultrason (rare) | +0,8 mA |
| Total arrondi | ~150 mA |
]
== Heltech ESP32 LoRa V3
#tablem[
  | Variante | Puissance |
|---|---:|
| Pi 3B+ (repos typique) | ~1,9 W |
| Pi 4B (repos typique) | ~2,7 W |
| AP Wi‑Fi (charge légère) | +0,3–0,7 W |
| LoRa HAT (Rx) | +0,06 W |
| Grove LCD (rétroéclairage) | +0,3 W |
| Ventilateur 5 V | +1,0 W |
| Autres (GrovePI, boutons, encodeur) | +faible |
| Total Pi 4B | ~4,5–4,8 W |
]
= Coups mensuels
- Electricité négligeable
- Difficiles à estimés
  - Dégradation du matériels
  - Couts des API
  - Scaling de l'architecture serveur.

A prioris, on pourrait dire que les couts augmenterais de façon linéraires avec les nombre d'utilisateur dans la flotte. Mais l'estimation est entièrement dépendante de la taille de la flotte déployée.
== Notes 
Ces données sont une aggrégation des consommation moyennes trouvée princpilement sur les site de e-commerces du type https://www.sparkfun.com. Pour une réel estimation un wattometer semblerais plus adapté ou même un oscilloscope pour les mesures les plus fines.
= Sécurités
== Les mots de passe hardcoder et faibles
Nos contre-mesures ont principalement conscisté pour 
==== Docker
Utilisation des variables d'environnements pour ne pas hardcoder de passwords directement dans les fichiers et l'utilisation de mots de passe non par défaut.

==== RaspberryPI
Pi n'utilise pas le mdp raspberry par défaut, mais un mots de passe autres.

==== Embarqué

Au niveau des ESP32, malheureusement les mdp sont hardcoder.
Pour ce qui est de l'arduino le code étant compilé complique sont accès.

Probablement que l'on aurait du utiliser du TLS plutot que du cryptage 100% symétrique. Et apparement les ESP32 ont un système de chiffrement du stockage intégrés. Nous n'avons malheuresement pas enquèter cette piste.

== Insecure Network Service
=== Lan
Le réseaux lan connectant les différentes n'est pas sécurité mais est consideré physiquement innacessible.

Une utilisation du 802.1x aurait pu sécurisé ce réseaux lan.

=== WIFI 
Notre RaspberryPi utilise dans son role d'access Point le wpa2, qui est tjrs sécurisé.
Une fois de plus une gestion centralisée des authentification type 802.1x par machine serait bien plus résistante.

=== Lora 
Cryptage AES, appliqué malheuresement avec un protocole daté, une utilisation de TLS sur le LoRa serait plus sécurisée.

=== Internet
Toute nos communications circulent sur des tunnels vpn crypté et sécurisé ce qui simplifie et sécurise la communication entre nos serveurs distants.

== Insecure Ecosystem Interfaces
=== Flask API 
Double sécurity utilise une clé api, et les routes passent à travers un reverse proxy permettant une vérification centralisée et securisée du chiffrement de nos communication api.

=== MQTT 
==== Client_id (outdated)
Vérification des clients
==== Authentification/Authorization
Utilisation de user/password pour pouvoir se subscribe et publish

C'est login donnent des accès partielle via les fichiers ACLs
==== TLS
Utilisation de certificats brokers et clients.

== Use of Insecure or Outdated Components
=== Utilisation de software à jour.
Utilisation d'une version de Rocky Linux toujours supporté par red hat et la communauté centos. 

Ce qui est d'autant plus important qu'elles sont partiellement exposée à internet.

=== Utilisation de Docker
On utilise les dernières versions stables de nos images, et pouvons facilements les mettres à jours avec docker compose,ainsi que des bump de version dans le fichier docker-compose.yml.

=== RPI
Le software n'est pas à jour.

- Le node-red a des failles de sécurité
- Debian buster n'est plus supporté 

La décision de ne pas le mettre à jour vient du fait que dexter Industries ne tiens plus à jour les librairies pour faire fonctionné le grovePI sur les Raspian moderne.

==== Mitigation
La raspberry n'a aucun accès à internet. Et ne peux communiquer que via le réseaux filaire ou son wifi sécurisé avec l'extérieur.

== Insufficient Privacy Protection
Notre concept de base, voulait garder toutes les données privée relative des utilisateurs sur les serveurs du client. Et à des flush automatiques des base de données (tous les 7 jours pour les localisation).
Cependant, nous avons dû ajouter un système de backup des données sur un serveur L3, pour pouvoir offrir des métriques publiques. Mais celle-ci sont anonymisée mais tout de même nettoyées tous les 30 jours.

Nous notons qu'avec plus de temps, un nettoyage plus profonds des logs devrait être implémentés. Même si ce ne sont pas des données privée.

== Insecure Data Transfer and Storage
Notre design n'as pas réelement de transfert non sécurisée à cause du chiffrement.

Par contre au niveau stockage, nous ne cryptons pas sur disque les base de données celà pour être effectivement une fonctionnalités.

Il est à noté que c'est base de données sont quand même sécurisée dans le sens que un seul utilisateur Linux y a accès (pour les remotes access) et pour ce qui est de l'accès root il est bloqué par défaut.

== Lack of Device Management

=== RaspberryPi
La RPI a un accès ssh à la layer 2, type bastion host est requis mais une fois obtenu connecté sur la L2, un accès VNC et SSH est disponibles.

=== Layer 2 
Permet un accès ssh depuis Internet. Le client peut décider de bloqué le port 22 au niveau de leur firewall nous gardons via ZeroTier.

=== Layer 3
Accès disponibles via internet, et le réseau SmartPedals

=== ESP32
Malheuresement les esp32 n'ont pas de fonctionnalités simmilaires.
J'ai lu que des Système d'OTA existait mais nous n'avons pas pousser les recherches.

== Insecure Default Settings
Les paramètres par défauts de notre architectures sont plutot sécurisé. Les utilisateurs doivent juste faire attention à bien modifié les mots de passe.
== Lack of Physical Hardening
=== RaspberryPi
Notre objectif dans une architecture non prototype serait de la placer en hauteur sur un piquet pour accroitre sa portée.

Et cela donnerait aussi une meilleur sécurité.

Ici notre prototype n'est pas sécurisé physiquement
=== Esp32 sur vélo
L'utilisation d'un devkit avec les pin de debugging accessible et un boitié pas intégré au cadre du vélo ne donne pas une sécuritée satisfaisantes, nous en avons conscience.

Cependant une réduction de la taille de l'appareil en utilisant un pcb personnalisé permettrait de faire un boitié adapté

=== Esp32 cadenas

Notre prototype en 3D donne une idée de à quoi pourrait ressembler le cadenas, mais il faudrait utilisé de l'acier épais et probablement bétonné le cadenas au niveau de la station.

=== Les serveurs
Il pourraient aisément être placé dans des data-center classiques dont cet aspet physique est moins problématiques.


= Note d'amélioration
