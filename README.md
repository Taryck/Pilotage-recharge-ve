# Pilotage de recharge VE — EV Load Manager

Intégration personnalisée Home Assistant disponible via HACS.

**v0.2.0 : pilotage automatique actif.** Après installation et redémarrage, cette intégration peut commander les bornes dès qu'un seuil est dépassé.

## Installation HACS

1. HACS → menu ⋮ → Dépôts personnalisés → ajouter `https://github.com/Taryck/Pilotage-recharge-ve`, catégorie **Intégration**.
2. Télécharger l'intégration puis redémarrer Home Assistant.
3. Paramètres → Appareils et services → Ajouter une intégration → **Gestion de recharge VE**.

Installation manuelle possible en copiant `custom_components/ev_load_manager` dans `config/custom_components/`.

## Configuration

- Installation : sélectionner les trois capteurs de courant total L1, L2 et L3 et saisir un seuil maximum en ampères pour chaque phase.
- Bornes : ajouter une sous-entrée **Borne de recharge** pour chaque borne ; renseigner les mesures L1/L2/L3 (facultatives), l'entité `number` ou `input_number` de réglage du courant, le minimum et le maximum en A, l'entité générique de commande (y compris `select` LocalTuya), ainsi que les deux valeurs exactes de démarrage et d'arrêt.
- Une phase de borne non configurée vaut 0 A ; une mesure configurée mais indisponible est invalide, pas égale à zéro. Unités gérées : mA, A, kA.

## Consultation

Trois capteurs par borne : état de surveillance (attributs des mesures et de la configuration), consigne initiale mémorisée et commande initiale mémorisée. Les valeurs initiales sont maintenant mémorisées avant la première réduction, conservées après chaque modification et stockées sur disque.

## Pilotage v0.2.0

- Dépassement calculé indépendamment sur L1, L2 et L3 ; déclenchement sur changement des entités et vérification toutes les 5 secondes.
- Choix de la borne ayant le plus fort courant mesuré sur une phase en dépassement, puis réduction de sa consigne par pas de 1 A. Recalcul après chaque acquittement.
- Mémorisation persistante de la consigne initiale, de l'état initial de commande et des courants initiaux par phase AVANT le premier changement.
- À défaut de retour de consigne dans les 12 secondes, tentative de commande d'arrêt. Une borne à son courant minimum peut également être arrêtée en dernier recours.
- Restauration progressive par pas de 1 A lorsque les phases disposent d'une marge de 2 A ; priorité à la borne la moins consommatrice puis à la plus fortement réduite. Redémarrage conditionnel d'une borne arrêtée par le module si la marge est suffisante pour son minimum.
- Un capteur global expose les courants, seuils et dépassements pour les trois phases.

**Attention : cette version envoie de vraies commandes de recharge.** Valider sur une instance de test ou avec des bornes simulées avant le déploiement en production. Une consigne acquittée dans HA ne démontre pas à elle seule le changement du courant physique.

## Tests

Exécuter `python -m pip install pytest` puis `python -m pytest -q tests`. GitHub Actions exécute la même suite aux push et pull requests. Les tests de décision ne remplacent pas des tests d'intégration avec les services HA.

**À approfondir :** comportement des indisponibilités, durée de réponse effective des bornes, cas de redémarrage, restauration après un arrêt et test de bout en bout.

## Validation

La configuration a été validée par l'utilisateur dans Home Assistant. Le nouveau pilotage nécessite encore une validation pratique sur une instance de test.

## Simulateur indépendant de parking VE

Le dépôt contient désormais une **seconde intégration HA** : [`ev_load_simulator`](custom_components/ev_load_simulator/README.md), installable manuellement dans `/homeassistant/custom_components/ev_load_simulator` sur une **instance de test**.

Elle propose un nombre de bornes configurable (1 à 100), toutes mono/tri configurables, des courants de départ fixes pendant le cycle, des cibles kWh, des commandes de charge, une rampe de réponse aléatoire de 1 à 2 A/s, du bruit indépendant ±0,2 A par phase et une commande d'arrêt à zéro en 2 secondes. Le multiplicateur ×5/×20 n'accélère que le calcul d'énergie.

Les capteurs `Parking Courant total L1/L2/L3` représentent **uniquement la somme des consommations des bornes**, sans autres charges. Seuils du compteur et temporisation avant coupure virtuelle configurables.

- [Scénarios JSON prédéfinis](custom_components/ev_load_simulator/scenarios.json)
- [Tests du modèle](tests/test_simulator.py)

Pour la procédure d'installation et la correspondance des entités avec `ev_load_manager`, lire le [README du simulateur](custom_components/ev_load_simulator/README.md). Le simulateur n'a pas encore été validé en situation réelle dans Home Assistant.
