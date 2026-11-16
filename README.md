# Pilotage de recharge VE — EV Load Manager

Intégration personnalisée Home Assistant disponible via HACS.

**v0.1.0 : configuration et lecture uniquement. Aucune consigne de recharge n'est envoyée.**

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

Trois capteurs par borne : état de surveillance (attributs des mesures et de la configuration), consigne initiale mémorisée et commande initiale mémorisée. Les snapshots disposent d'une structure de stockage persistante, mais ne sont pas encore alimentés : les deux derniers capteurs restent inconnus jusqu'à l'implémentation du pilotage.

**Hors périmètre de la v0.1.0** : réaction à un dépassement, commandes vers les bornes, restauration automatique, interface Lovelace spécifique.

## Validation

Syntaxe Python initialement contrôlée. L'exécution du flux de configuration dans une version récente de Home Assistant nécessite encore un test réel.
