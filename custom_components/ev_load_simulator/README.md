# Simulateur de parking VE : version intégration HA

L'intégration `ev_load_simulator` remplace le précédent package YAML `tests/simulator/ev_test.yaml`. Elle expose un compteur total **exclusivement constitué de courants de recharge**, sans charge domestique fictive.

## Installation

**Sur une instance Home Assistant de test distincte :**

1. Copier le dossier du dépôt `custom_components/ev_load_simulator` vers `/homeassistant/custom_components/ev_load_simulator` (ou `/config/custom_components/ev_load_simulator` selon l'installation).
2. Supprimer ou désactiver l'ancien package `ev_test.yaml` si tu n'en as plus besoin : ses entités ne servent pas à la nouvelle simulation.
3. Redémarrer HA, puis **Paramètres → Appareils et services → Ajouter une intégration → Simulateur parking VE**.
4. Paramétrer : nombre de bornes **1 à 100** (10 par défaut), temps accéléré pour l'énergie, trois seuils de coupure du compteur virtuel, durée de dépassement maximale en secondes, graine aléatoire.
5. Installer également l'intégration existante `ev_load_manager` sur cette instance de test, sans la connecter aux équipements physiques.

**ATTENTION** : le HACS du dépôt référence l'intégration principale. Pour être certain d'installer le simulateur également, copier son dossier manuellement selon les étapes ci-dessus.

## Raccordement au module de pilotage

Dans la configuration principale de **Gestion de recharge VE**, choisir :
- `sensor.parking_courant_total_l1`, `sensor.parking_courant_total_l2`, `sensor.parking_courant_total_l3` comme courants totaux du parking. Les courants sont les sommes des phases mesurées de toutes les bornes virtuelles.
- Utiliser les mêmes seuils L1/L2/L3 que le simulateur pour les essais où l'on veut vérifier que le pilote évite la coupure virtuelle.

Pour chaque borne, sélectionner :
- courant L1/L2/L3 : capteurs `sensor.ve_sim_borne_01_courant_l1`, `...l2`, `...l3`
- réglage : `number.ve_sim_borne_01_consigne_courant`
- commande : `select.ve_sim_borne_01_charging_command`
- démarrer : **Charge en cours**
- arrêter : **Charge Terminée**

Le domaine et le nom exact de l'entité peuvent recevoir un suffixe HA en cas de doublon : vérifier les IDs réels dans **Outils de développement → États**.

Le minimum / maximum réglé dans le gestionnaire reste **indépendant** des valeurs `number.ve_sim_borne_01_minimum_materiel` et `...maximum_materiel` du simulateur.

## Démarrer les cycles de charge

Chaque borne a :
- `number.ve_sim_borne_01_courant_de_depart` : **consigne de départ**, conservée pendant tout le cycle, même après réduction par le gestionnaire.
- `number.ve_sim_borne_01_cible_energie` : cible kWh modifiable.
- `select.ve_sim_borne_01_mode_de_charge` : Triphasé, Monophasé L1, L2 ou L3.
- `button.ve_sim_borne_01_demarrer_cycle` : démarre un cycle en remettant l'énergie de ce cycle à zéro.
- `sensor.ve_sim_borne_01_energie_delivree` : énergie calculée ; à la cible, la borne s'arrête en deux secondes réelles.

Le courant mesuré progresse **aléatoirement de 1 à 2 A par seconde réelle** jusqu'à la consigne de charge (en montée ou descente) ; chaque phase active présente un écart aléatoire de ±0,2 A. Une consigne **inférieure au minimum matériel** est ignorée ; le contrôle n'obtient pas l'acquittement attendu. On peut aussi configurer `select.ve_sim_borne_01_simulation_de_panne` sur **Ignore consignes**.

La commande d'arrêt ne suit pas la rampe habituelle : elle provoque la baisse à zéro en **2 secondes réelles**. La reprise d'une session arrêtée conserve l'énergie déjà délivrée.

Le multiplicateur de temps **×5, ×20, etc. agit seulement sur l'énergie**. Il n'accélère ni les rampes de courant ni la temporisation de coupure.

## Lancer dix bornes (ou davantage)

- Bouton global **Parking Lancer cycles aléatoires** : attribue mode, courant initial, minimum matériel et objectif énergétique, puis lance environ 85 % des bornes. Un même seed crée une séquence reproductible depuis le début du simulateur.
- Sélecteur global **Parking Scénario de test** : sélectionner un ID du fichier `scenarios.json`.
- Bouton global **Parking Appliquer scénario de test** : charge les paramètres du scénario et démarre les bornes listées.

Certains scénarios exigent davantage de bornes : pour `number_of_chargers`, configurer le nombre à **24** avant de l'appliquer.

Les configurations sont documentées dans `scenarios.json`, notamment : `mono_phase_l2`, `ten_triphase`, `mixed_modes`, `hardware_min_mismatch`, `energy_completion`, `breaker_timeout`, `unresponsive`, `number_of_chargers`.

## Coupure simulée

Le compteur virtuel mesure chaque seconde si **au moins une phase** dépasse son seuil. La durée de dépassement est cumulée **tant que le dépassement persiste** et réinitialisée dès que toutes les phases reviennent sous leurs limites. Après la durée maximale, le disjoncteur virtuel passe à **coupure**, et les bornes commencent leur arrêt de 2 secondes. Un bouton permet de réarmer le disjoncteur.

Cette coupure n'agit sur **aucune borne physique**.

## Tests

```bash
python -m pip install pytest
python -m pytest -q tests
```

Le workflow CI du dépôt exécute aussi `tests/test_simulator.py`. Les tests unitaires couvrent modèle physique, compteurs, monophasé/triphasé, rampes, bruit de mesure, minima matériels, fin de cycle, coupure temporisée et reprise sans perte de kWh.

**Limite actuelle :** le simulateur est un premier développement ; sa compatibilité et ses identifiants d'entités n'ont pas encore été vérifiés en instance HA réelle. Le comportement du contrôleur à l'aide du simulateur devra faire l'objet de tests d'intégration dédiés.
