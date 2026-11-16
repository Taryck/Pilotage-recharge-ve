# Banc d'essai de bornes virtuelles (Home Assistant)

Ce **package Home Assistant** fournit deux bornes virtuelles et un réseau triphasé virtuel. **Ce n'est pas un simulateur Zigbee** et il ne communique avec aucun appareil physique. Il sert à tester l'intégration `ev_load_manager` au niveau exact où celle-ci travaille : les entités et les services HA.

## Installation

1. Utiliser une **instance Home Assistant de test**, distincte de la production. L'intégration v0.2.0 pilote réellement ses entités configurées : **ne pas laisser les vraies bornes dans sa configuration pendant l'essai**.
2. Copier `ev_test.yaml` dans `/config/packages/ev_test.yaml`.
3. Ajouter dans `configuration.yaml` (en fusionnant avec la section `homeassistant:` existante, sans la dupliquer) :

```yaml
homeassistant:
  packages: !include_dir_named packages
```

4. Vérifier la configuration et redémarrer Home Assistant.
5. Dans une **nouvelle configuration de Gestion de recharge VE** sur cette instance, sélectionner les entités suivantes :

| Rôle | Entité |
|---|---|
| Courant total L1 | `sensor.ev_test_installation_l1` |
| Courant total L2 | `sensor.ev_test_installation_l2` |
| Courant total L3 | `sensor.ev_test_installation_l3` |
| Seuils L1/L2/L3 | **20 A** chacun |
| Borne mono : L1 | `sensor.ev_test_mono_l1` |
| Borne mono : L2 | `sensor.ev_test_mono_l2` |
| Borne mono : L3 | `sensor.ev_test_mono_l3` |
| Borne mono : réglage | `input_number.ev_test_mono_consigne` |
| Borne mono : commande | `input_select.ev_test_mono_commande` |
| Borne tri : L1 | `sensor.ev_test_tri_l1` |
| Borne tri : L2 | `sensor.ev_test_tri_l2` |
| Borne tri : L3 | `sensor.ev_test_tri_l3` |
| Borne tri : réglage | `input_number.ev_test_tri_consigne` |
| Borne tri : commande | `input_select.ev_test_tri_commande` |

Pour les deux bornes : minimum **8 A**, maximum **32 A**, valeur de démarrage `Charge en cours`, valeur d'arrêt `Charge Terminée`.

## Essai du dépassement

Au démarrage : autres usages 3 A sur chaque phase ; chaque borne tire 10 A. Installation : **L1 = 23 A, L2 = 13 A, L3 = 13 A**. Il y a donc immédiatement un dépassement de **3 A sur L1** et le régulateur peut se déclencher. **Pour éviter cela au premier chargement**, réduire `input_number.ev_test_maison_l1` à 0 A **avant d'activer le pilotage**, ou régler le seuil L1 à 30 A puis le baisser à 20 A pour provoquer le dépassement.

Pour observer le test de façon contrôlée :
1. Régler les trois seuils à 30 A (les valeurs initiales donnent 23/13/13 A).
2. Régler L1 à 20 A : dépassement 3 A → réductions successives de 1 A sur les bornes qui tirent sur L1.
3. Observer `input_number.ev_test_mono_consigne`, `input_number.ev_test_tri_consigne`, et les capteurs de consigne initiale mémorisée.
4. Remettre L1 à 30 A : marge de restauration ; les consignes remontent progressivement.
5. Pour faire fonctionner la borne tri **en monophasé**, changer `input_select.ev_test_tri_mode` en `Monophasé L1`. Ses capteurs L2 et L3 passent à 0 A.
6. Modifier `input_number.ev_test_maison_l2` et L3 pour créer des dépassements simultanés sur plusieurs phases.

## Limites de simulation

La commande et sa mesure répondent instantanément. Le package ne reproduit pas de latence Zigbee, de défaut réseau, de borne qui ignore les commandes ou de décalage entre consigne et courant réel. Ces tests nécessiteront des mécanismes de panne supplémentaires. Les capteurs virtuels ne valident **pas** le comportement de LocalTuya ou du matériel réel.

Le package n'est **pas** installé automatiquement par HACS : il doit être copié dans `packages` sur l'instance de test.
