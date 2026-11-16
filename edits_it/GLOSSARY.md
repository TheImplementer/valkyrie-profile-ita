# Italian translation: wording choices

The terms, names and phrasings chosen for `edits_it/`, and why. Keep new translations consistent
with this list, and update it when a choice changes. (Builds and the GUI read only the `*.txt` files
here, so this file is never built into the disc.)

## Names

| English | Italian | Note |
|---|---|---|
| Valkyrie (speaker label) | Valkyrie | Kept as the character's name above her lines. |
| a Valkyrie (in a line) | una Valchiria | The common noun is translated: Lucian, scene 0x13 #30. |
| Lenneth, Freya, Frei, Loki, Lucian, Platina, Arngrim, Aelia | unchanged | Personal names stay as in the English release. |
| Odin (speaker label) | Odin | Kept as the label. |
| Lord Odin (in a line) | sommo Odino | Odin takes the Italian form in speech. |
| Mother Laia | Madre Laia | Speaker label in the title-screen prologue. |
| Midgard, Valhalla, Asgard, Einherjar, Ragnarok, Mimir | unchanged | Norse names stay. |
| the Vanir / the Aesir | i Vani / gli Asi | The usual Italian names of the two god races (scene 0x1C). |
| Weeping Lilies | Gigli del Pianto | Proper name of the poisonous flowers (scene 0x10). |
| Seraphic Gate | Porta Serafica | Title menu and table 6. |
| spiritual concentration | concentrazione spirituale | World map (`worldmap.txt`). |
| the departed / the dead | i defunti | Freya's lesson. |
| the undead | i non morti | Freya's lesson. |
| Press START button to… | Premi START per… | The START prompt on the world map. |
| Artifact | Artefatto | System messages (`system.txt`); capitalised as in the English. |
| Evaluation Level | Valutazione | In messages; "Valut." in menus. |
| Event EXP | punti esp. evento | "You earned N Event EXP." → "Hai ottenuto N punti esp. evento." |
| To the rear / To the front | Indietro / Avanti | System records 9 and 10. Not yet seen in play, so the context is a guess. |
| Battle | Battaglia | The label at the top left (system record 13). |
| Roland, Jelanda, Angela, Badrach, Lombert, Lawfer, Celia, Kashell, Villnore, Artolia, Yamato | unchanged | Arngrim's chapter (scenes 0x63-0x74, 0x0B). |
| Battle-Maiden | Vergine della Battaglia | "La Vergine della Battaglia, Valkyrie?" (scene 0x6E). |
| Chief Councillor | Gran Consigliere | Lombert's title. |
| Sir Arngrim | ser Arngrim | Used by Angela and the King. |
| Lady Angela (Arngrim, mocking) | madamigella Angela | |
| Ghoul Powder / Ghoul | Polvere di Ghoul / ghoul | Necromancer → negromante. |
| 10,000 deaths are not enough for you!! | Diecimila morti non ti (vi) basterebbero!! | Jelanda's catchphrase, four times: keep it identical. |
| little one (Arngrim to Jelanda) | piccola | |
| "Law" in Lawfer's name | "Law", la legge | Scene 0x73: the pun is explained, not translated. |
| To outside (door sign) | Uscita | Signposts are drawn in a 192-pixel box (op 0x98). Chapel → Cappella, Tavern → Taverna, Private Residence → Casa privata, Main Street → Via maestra. |
| Dragon Slayer | unchanged | Weapon names are not translated yet; "You got X" → "Ottieni X". |
| Save game? | Salvare? | Kept short. |

Speaker labels that are roles are translated: Agente, Ragazza, Cameriera, Capocuoco, Cavaliere (di Artolia),
Soldato, Re, Capitano (Knight Captain), Donna, Giovane, Anziano / Anziana, Avventuriero, Uomo.

## Forms of address

* **Odin is addressed formally:** *voi*, "mio signore", "al vostro servizio".
* **Valkyrie, Freya and Frei use *tu*** with each other.
* **Platina addresses Madre Laia's guest formally:** "I vostri vestiti…", "Perdonatemi…".
* **Platina and Lucian call her parents *mamma* and *papà*:** "Mother and Father" becomes "Mamma e papà".
* **Arngrim's chapter:** brothers, mercenaries, Lombert to Arngrim and Valkyrie to humans use *tu*.
  Subjects address royalty with *voi* (Lombert to Jelanda, Jelanda's spirit to Valkyrie). Arngrim gives
  Angela an ironic *voi* until she is unmasked, then calls her *piccola* with *tu*. He mocks the King
  with *voi*. Jelanda uses *tu* downward (waitress, Lombert, Arngrim), and Lawfer uses *voi* with his
  father, the Knight Captain.
* **Badrach speaks colloquially** ("un sacco", "chi se ne frega", "Ci si vede!"), without dialect spellings.

## Title screen (`title_menu.txt`)

| English | Italian | Note |
|---|---|---|
| New Game | Nuova Partita | |
| Continue | Continua | |
| Configuration | Opzioni | Shorter than "Configurazione", and it matches the camp menu's Opzioni. |
| Sound Mode | Colonna Sonora | The user's choice, over Musiche, Sonoro and Suoni. |
| Prologue | Prologo | |
| Opening Movie | Filmato Iniziale | |
| Seraphic Gate | Porta Serafica | |

The same words are used in table 6 (`menu_6.txt`, messages 0x64-0x6C), which holds the italic titles
at the top of the New Game, Continue, Configuration and Sound Mode screens. Those first read "Nuova
partita", "Configurazione" and "Modalità audio" and were aligned with the title labels.
Seen in TinyPSX: `notes/edit_proof/italian_title_screens.png`.

## Menus and battle

| English | Italian | Note |
|---|---|---|
| Divine Items | Reliquie | "Oggetti divini" ran into the next label (Gruppo). |
| Skills / Abilities | Tecniche / Abilità | |
| Equip / Equipment | Equipaggia / Equip. | "Equipaggiamento" was cut off in its column. |
| Text Speed | Vel. testo | "Velocità testo" touched its value. |
| Materialize Points | Punti materia | |
| Evaluation | Valut. | |
| EXP, EXP Orb | esp. (Punti esp., Sfera esp.) | |
| DME | DME | Kept, explained once as "(energia divina)". |
| Fire, Ice, Lightning, Poison, Holy, Dark | Fuoco, Ghiaccio, Fulmine, Veleno, Sacro, Oscurità | Elements. |
| None | Nessuno | |
| Elixir, Remedy, Union Plume | Elisir, Rimedio, Piuma dell'Unione | Item names (battle 1806.1); "Primo"/"Nobile" for the higher grades. |
| place names (world map) | generic part translated, proper name kept | e.g. Rovine di Monte Artolia, Grotte di Oddrock, Torre del Sogno Nero. |

## Lines shortened to fit a fixed box

Menu labels are drawn at fixed places, and some dialogue boxes have a fixed width that the build
can't grow. A line wider than its box wraps or runs into its neighbour, so these lines were
shortened until they fit on screen:

| Where | Longer version | Used |
|---|---|---|
| scene 0x9 #15 | Come puoi dire una cosa simile!? | Come puoi dire così!? |
| scene 0x10 #16 | …il Paradiso? | …il Cielo? |
| scene 0x11 #20 | Sono così felice di averti conosciuto, | Sono felice di averti conosciuto, |
| scene 0x11 #20 | dimenticare… Dimenticare… tutto… quanto… | dimenticare… tutto… |
| scene 0x11 #14 | Non lo permetterò!! | Non voglio!! (kept no wider than the English) |
| system record 11 | Tornare alla mappa del mondo? | Tornare alla mappa? (its box is 140 px) |
| Party menu (2182) | Modifica, Schiera | Forma |
| Use Item | Equipaggiamento | Equip. |
| Options | Velocità testo | Vel. testo |
| Options | the Dash help line | shortened to fit its box |

## Conventions kept from the English text

* The 「 opening bracket on spoken lines, and the game's ・・・ and ... ellipses as they were.
* Every control code (`<3:..>` typing delays, `<12:..>` voice clips, `<17:..>` waits, `<18>`,
  `<19:..>`, `<7:..>` widths) stays where it was relative to the words it times.
* A line with no words (only ・・・) is left out of the edit file, so it stays byte for byte as it was.
* A line that stays in English on purpose is listed in its edit file unchanged, so `vp_progress.py`
  counts it as done. `build` leaves such an entry byte for byte as it was. This covers:
  * names only, such as Platina's 「Lucian?」 and 「Lucian...」;
  * the character and place-name lists (menu 6, linked to the camp menus; battle 1500.9 0x01-0x19);
  * speaker labels in the battle hints (1988.2);
  * DME, Stereo, ON and OFF.
  * "Scenario Error 1" (system record 12, a debug message).

  Still left out, so they count as not done: "Iseria Queen" and "Wraith" in the name list (they may
  need translating), LV and CT, and the monster names.
