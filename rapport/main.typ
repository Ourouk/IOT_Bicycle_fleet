#import "template.typ": *

#show: project.with(
  course-title: "IOT",
  date: datetime.today(),
  title: "Vélos connectés\nVersion 2",
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

== Diagramme
#set page(flipped: true, margin: 2.5%)
#figure(image("figures/IOT.png",width: 100%),)

#set page(flipped: false,
    margin:  (x: 3cm, top: 2cm, bottom: 2cm),header-ascent: 35%,
  )
= Layer 1
== Matériel au dessus de la station (Raspberry PI)

=== Wifi Access Point
Objectif:
- Connection avec les ESP32
==== Configuration as Wifi AP
Liste des outils utilisés :
- hostpad - utilitaire permettant d'utiliser la rpi comme un access point
- dnsmasq - simple serveur dhcp
- interface - mettre les addresses ip fixes

Les modification apportées à raspbian sont disponibles sur le git du projet /raspberry/root/.
=== NodeRed
- Node crypto-blue pour gérer le déchiffrement des packets lora.
- Node mqtt -> Communication L2/Esp32 WIFI
- Node Serial -> Lecture/écriture sur le dragino la66 usb lora
- Node fonction pour le parsing
- Node GrovePi : Communication avec les différent capteurs 
- Node 
Les flows sont disponnibles sur le github /raspberry/root/home/pi/.nodered
=== Mosquitto
- Instance publique sans sécurité spécifique pour gérer les communication avec l'esp32

== Matériel Embarqué (Helltech Lora ESP32)
#figure(
    grid(
        columns: 2,
        gutter: 2mm,
        image("hetecLoraV3_back2.png", width: 70%),
        image("heltecLoraV3_IO.png", width: 100%)
    )
)
== Communication LoRa RaspberryPI - Heltec Lora V3

== Matériel sur station (ESP32 Wifi)




=== Filaire jusqu'au serveur
= Layer 1 to 2
Pour gérer les communications entre les objets embarqués et les serveurs, nous utilisons le protocol mqtt avec plusieurs topic.
== Encodage

- Pour simplifier la lisibilité et le traitement des informations encodées, les antennes RPI communique avec le serveur L2 en utilisant du json
  - Example bike communication
  ```
  {
    "device_id": 1,
    "type": "location",
    "timestamp": "2023-10-01T12:00:00Z",
    "satellites":3,
    "coordinates": {
      "lat": 50.8503,
      "lon": 4.3517
    },
  }
  ```
  - Example station communication
- Auth request
```json
  {
    "bike_id": 1,
    "type": "auth",
    "action": "unlock"
    "user_id": "andrea98"
    "timestamp": "2023-10-01T12:00:00Z",
  }
  {
    "bike_id": 1,
    "type": "auth",
    "action": "lock"
    "user_id": "andrea98"
    "timestamp": "2023-10-01T12:00:00Z",
  }
```
- Reply
```json
  {
    "bike_id": 1,
    "type": "auth_reply",
    "action": "accept"
    "user_id": "andrea98"
    "timestamp": "2023-10-01T12:00:00Z",
  }
  {
    "bike_id": 1,
    "type": "auth_reply",
    "action": "deny"
    "user_id": "andrea98"
    "timestamp": "2023-10-01T12:00:00Z",
  }
```
= Layer 2
== Rocky Linux
=== Docker CE
=== Zerotier
== Containers
=== Mongodb
! A l'intégrité !
==== Table Users
==== Table Bikes
==== Table Stations
 
= Layer 3
== Rocky Linux
=== Replication of Layer 2
== Containers
=== NodeRed

= Layer 4
== WebEx
== Twilio
== Shodan
== Maps Provider
== ZeroTier
== Broker public MQTT

= Note d'amélioration
== RPI 
La version de raspian utilisée n'est plus supportée et ne recçois plus de mise à jour de sécurité depuis le 30 Juin 2022. #cite(<noauthor_debian_nodate>)

De ce problème d'autre émerge la version de nodered n'est plus supportée, et tous les outils systèmes n'ont eu aucun patch de sécurité appliqué.

Heureusement le problème est mitigé par l'absence de connection directe à Internet

Solutions :
  - Mise à jour vers rapsiban buster
  - Hébergement d'un repo sur le serveur L2 pour proposé les mise à jour à la flotte d'antenne. 
  - Se débarasser de grovePI et utiliser un esp/arduino à la place.

