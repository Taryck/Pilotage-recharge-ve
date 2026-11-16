# Pilotage de recharge VE — EV Load Manager

Intégration personnalisée Home Assistant (HACS) pour répartir le courant disponible entre plusieurs bornes de recharge, sur une installation monophasée ou triphasée.

**Version 0.3.0 : contrôleur non bloquant, réduction par vagues et délais configurables.**

> Cette intégration vise à limiter les dépassements de courant mesurés, mais **ne remplace pas un disjoncteur, ni une protection électrique réglementaire ou un délesteur certifié**. En cas de panne, redémarrage ou indisponibilité de Home Assistant, aucune régulation n'est garantie.

## Installation HACS

1. HACS → Dépôts personnalisés → ajouter `https://github.com/Taryck/Pilotage-recharge-ve` de type **Intégration**.
2. Télécharger ou mettre à jour, puis redémarrer Home Assistant.
3. Paramètres → Appareils et services → **Gestion de recharge VE**.
4. Configurer l'installation, puis ajouter chaque borne comme sous-entrée.

Les configurations manuelles et les configurations importées depuis le simulateur sont indépendantes. Une configuration de test ne modifie pas les compteurs ou les bornes physiques.

## Configuration de l'installation

| Champ | Utilité |
|---|---|
| **Courant total L1, L2, L3** | Trois capteurs HA du courant total *réellement mesuré* sur chaque phase. Ils doivent inclure les bornes. Pour le parking simulé : trois capteurs virtuels du simulateur. |
| **Seuil maximal L1, L2, L3 (A)** | Courant autorisé par phase ; un dépassement est calculé indépendamment sur chaque phase : `max(0, courant_mesuré - seuil)`. |
| **Délai d'acquittement par tentative (s)** | Durée pendant laquelle on attend que **l'entité de consigne** confirme le changement demandé. Défaut **6 s**, plage 1–120 s. **N'empêche jamais d'agir sur les autres bornes.** |
| **Nombre de nouvelles tentatives** | Réémissions de la *même* commande avant un ordre d'arrêt si elle ne s'acquitte pas. Défaut **2** (donc 3 commandes au total), plage 0–10. |
| **Intervalle de contrôle (s)** | Vérification périodique même sans nouveau changement d'état. Défaut **1 s**, plage 0,2–30 s. |
| **Attente après acquittement (s)** | Délai propre à chaque borne avant une **nouvelle modification de sa consigne**, laissant au courant physique le temps d'évoluer. Défaut **1 s**, plage 0–30 s. |

Il n'existe **plus de pause globale de 5 ou 12 secondes par ampère**. Le délai d'acquittement ne s'applique qu'à la borne concernée. Attention : recevoir la nouvelle valeur dans HA ne prouve pas que le courant physique a déjà diminué.

Les capteurs de courant acceptent les valeurs en A, mA et kA (converties en A). Une mesure configurée mais indisponible est considérée comme invalide et non comme un zéro.

## Configuration de chaque borne

| Champ | Utilité |
|---|---|
| **Nom** | Nom descriptif de la borne. |
| **Courant L1, L2, L3** | Capteurs mesurant le courant réellement tiré par la borne sur chaque phase. Une phase non utilisée peut être laissée non configurée (0 A), mais une mesure configurée devenue indisponible reste invalide. Les bornes triphasées fonctionnant temporairement en mono sont prises en compte par leurs mesures. |
| **Courant minimal / maximal (A)** | Valeurs de consigne permises par le régulateur. Doivent correspondre au matériel. Si une borne refuse une valeur, les tentatives et l'arrêt sont appliqués. |
| **Entité de consigne** | Entité modifiable `number` ou `input_number` recevant `set_value`, par pas de 1 A. |
| **Entité de commande** | Entité `select`, `input_select`, `switch`, `input_boolean`, `number` ou `input_number` commandant marche/arrêt. |
| **Valeur de démarrage / d'arrêt** | Valeurs exactes envoyées à l'entité de commande. Exemple LocalTuya : `select.marquise_borne_charging_command` avec les valeurs réellement proposées par la liste des options. Pour un switch : `on` / `off`. |

Attention : si le minimum déclaré dans l'outil est **inférieur** au vrai minimum matériel, la borne peut refuser une réduction et le module peut finir par lui envoyer l'arrêt.

## Logique du pilotage

### 1. Surveillance par phase

Sur événement de capteur ou à chaque intervalle de contrôle, on calcule les dépassements L1, L2 et L3. Si l'un des trois courants généraux est indisponible, aucune nouvelle décision de régulation fondée sur des mesures incomplètes n'est prise.

### 2. Réduction *par vagues*, sans attendre chaque borne

1. Détecter les phases en dépassement et identifier les bornes qui y tirent du courant.
2. Les classer selon leur **courant mesuré décroissant sur les phases dépassées**.
3. **Avant la première modification d'une borne**, mémoriser et enregistrer sur disque sa consigne initiale, sa commande initiale et ses trois courants initiaux. Cette référence n'est pas remplacée à chaque nouveau pas.
4. Envoyer **jusqu'à une baisse de 1 A par borne éligible dans la même vague**. Les réductions sont planifiées pour compenser le dépassement estimé sur chacune des phases. Une borne triphasée allège potentiellement trois phases avec une seule baisse.
5. Les commandes sont envoyées **sans attendre l'acquittement des autres bornes**. Chaque borne suit son propre délai, ses tentatives et sa période de stabilisation.
6. À la vague suivante, relire les courants effectivement mesurés, recalculer les dépassements et reclasser les bornes. Une borne ayant déjà une commande en attente ne reçoit pas un deuxième pas concurrent.
7. Si la borne n'acquitte pas après toutes les tentatives, demander son **arrêt total** et signaler tout échec d'arrêt. Si toutes les bornes pertinentes ont atteint leur minimum et qu'il reste un dépassement, l'arrêt est possible en dernier recours.

Exemple : avec un dépassement de 8 A sur L1 et trois bornes tirant 16, 12 et 10 A sur L1, la première vague peut envoyer **trois commandes de -1 A** sans attendre 6 secondes entre elles ; les vagues suivantes se basent sur les nouveaux courants.

### 3. Restauration progressive

En l'absence de dépassement, lorsque les phases utilisées par une borne disposent de **2 A de marge**, le contrôleur peut remonter sa consigne **de 1 A**. Ordre : **courant mesuré croissant**, puis **réduction cumulée décroissante**.

Les bornes **arrêtées par le module** sont considérées en priorité pour un redémarrage, sous réserve de la marge nécessaire pour leur consigne courante ; elles ne doivent pas rester indéfiniment en attente pendant que d'autres bornes remontent.

La restauration ne dépasse ni la consigne initiale mémorisée ni le nouveau maximum configuré. Le snapshot est supprimé lorsque la cible restaurable `min(consigne_initiale, nouveau_maximum)` est atteinte, même si le maximum a été réduit entre-temps.

### 4. Fin de recharge : suppression de la valeur initiale

**Dès qu'une borne reste indiquée comme active mais que ses trois phases mesurent 0 A, les anciennes valeurs mémorisées de charge sont effacées.** Il n'y a plus de recharge à restaurer. L'état absent d'une phase non configurée vaut 0 A ; une phase configurée mais indisponible ne déclenche pas cette suppression.

Exception temporaire : une borne que le module vient lui-même de redémarrer peut rester à 0 A pendant sa rampe physique de reprise ; une courte fenêtre de grâce évite d'effacer son état immédiatement. Une borne volontairement arrêtée et non arrêtée par le module perd également son snapshot.

### 5. Diagnostic et erreurs

Une exception dans le contrôleur est journalisée et fait apparaître une **notification persistante Home Assistant** ; les cycles suivants peuvent reprendre. Les pertes de mesures ou l'absence d'acquittement sont surveillées séparément par borne.

Le stockage `.storage/ev_load_manager_<entry_id>` est contrôlé à la lecture. Un contenu anormal empêche le chargement de l'intégration, afin de ne **pas perdre silencieusement** les consignes mémorisées.

Un capteur d'installation expose les courants, seuils et dépassements. Chaque borne dispose de capteurs d'état et de mémorisation. Les états techniques sont stables (`normal`, `overload`, `unavailable`, `monitoring`) et les libellés sont traduits en français/anglais.

## Utilisation avec Charging Station Simulator

1. Installer [Charging Station Simulator](https://github.com/Taryck/Charging-Station-Simulator).
2. Dans les options du simulateur, choisir **Synchroniser avec Pilotage recharge VE**.
3. Vérifier les trois capteurs de courant virtuels, les seuils et les bornes à ajouter.
4. Confirmer pour créer ou actualiser **uniquement l'installation de test**.

Les IDs des capteurs proviennent du registre HA (renommages acceptés) et les sous-entrées gardent un identifiant stable. Une deuxième synchronisation ne duplique pas les bornes. Les options ajustées manuellement dans le pilotage, notamment les délais et les seuils, **sont conservées** lors d'une resynchronisation : elles restent prioritaires sur les valeurs importées. Les bornes retirées du simulateur ne sont pas automatiquement supprimées.

## Tests et limites

Lancer `python -m pip install pytest && python -m pytest -q tests`. Le dépôt contient des tests du planner, de l'import du simulateur **et du contrôleur avec un faux runtime HA** (commandes concurrentes, acquittements, tentatives, arrêt, snapshots, redémarrage, erreurs). Les tests sont aussi définis dans `.github/workflows/tests.yml`.

**Le chargement et le comportement de cette nouvelle version sur une instance Home Assistant réelle ne sont pas encore validés.** Faire d'abord un essai sur le parking simulé et vérifier les journaux, les courants et les commandes. Aucun test logiciel ne remplace la validation des protections électriques.
