#import "template.typ": *

#show: project.with(
  course-title: "IOT",
  title: "Vélos connectés",
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
Nous ajouterons également quelques fonctionnalités intelligentes pour améliorer le confort de l'utilisateur.

== Proposition

Le projet consiste à réaliser trois objets connectés avec des capteurs différents. Ces objets sont les suivants :
- Un vélo connecté avec un ESP32 compatible LORA, intégrant une gestion des lumières en fonction de la luminosité, un buzzer, un GPS et un lecteur RFID pour prévenir le vol;
- Une station de sécurité/charge connectée avec un ESP32 compatible WIFI, comprenant une détection de la présence ou non d'un vélo et un système de déverrouillage par badge;
- Une antenne connectée avec un edge processing basé sur un Raspberry Pi se connectant aux deux autres objets et agissant comme un point relais ou un hub.

Le serveur de gestion sera réalisé en Python avec une base de données MongoDB.
Un serveur sera présent côté client de notre produit, et un second sera géré de notre côté pour la distribution de mises à jour, le dépannage et la télémétrie.

Les deux serveurs seraient basés sur Rocky Linux en utilisant une architecture containerisée avec Docker.

== Diagramme
