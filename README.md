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

## Synchronisation avec Charging Station Simulator (v0.2.1)

Le module supporte maintenant **plusieurs installations** configurées indépendamment.
Une installation de test peut être créée sans remplacer les capteurs physiques de
l'installation existante.

Installer d'abord [Charging Station Simulator](https://github.com/Taryck/Charging-Station-Simulator).
Dans **Paramètres → Appareils et services → Charging Station Simulator → Configurer → Synchroniser avec Pilotage recharge VE** :
lire l'aperçu des trois compteurs et des bornes à ajouter ou actualiser, puis confirmer.

La synchronisation utilise le registre des entités Home Assistant (les IDs renommés
sont pris en compte), les seuils du simulateur, ses minimums/maximums matériels,
le réglage `number` et la commande `select` (Charging / Stopped). Une entrée
distincte de pilotage est créée pour **ce parking simulé uniquement**.

Répéter l'opération n'ajoute pas de doublons. Les bornes qui n'existent plus dans le
simulateur ne sont pas supprimées automatiquement. Les autres installations,
notamment physiques, ne sont pas modifiées.

**Attention :** un test ainsi relié active le régulateur de la nouvelle installation
simulée. Il peut commander les `number` et `select` virtuels dès l'apparition d'un
dépassement. Tester sur une instance HA dédiée de préférence.

Tests de l'importation : `tests/test_simulator_import.py`.
