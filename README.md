# Šachová aplikace – přehrávání PGN + analýza enginem

Desktopová aplikace (PySide6 / Qt) pro:

- zobrazení **šachovnice** a hraní vlastních tahů,
- **přehrávání partií z PGN** tah po tahu (dopředu, zpět, na začátek/konec, plynulé přehrávání),
- **analýzu pozice** libovolným UCI enginem (Stockfish apod.) v samostatném vlákně,
  se **svislým ukazatelem hodnocení** vedle šachovnice,
- **rozbor celé partie enginem** – barevné značky nepřesností/chyb/hrubek u tahů,
  přesnost (lichess vzorec) a průměrná ztráta (ACPL) pro obě strany,
- **rozbor přesnosti přes databázi** (karta *Přesnost*) – přesnost, ACPL, ztráta
  v očekávaných bodech, hrubky/100 tahů, shoda s enginem (T1), kompozitní index,
  odhad výkonnosti (IPR), charakter partií (volatilita, ostrost, komplexita)
  a dotahování vyhraných/prohraných pozic, s rozpadem podle barvy, fáze, tempa
  a roku; výsledky se cachují,
- **rozbor hráče** nad databází partií: strom zahájení z aktuální pozice, heatmapa tahů,
  rozbor zahájení podle ECO (+ **diverzita repertoáru** a **hloubka teorie/book exit**
  z vlastní historie hráče) a rozbor koncovek podle kategorií (vše s úspěšností),
- **statistické vzorce** (karta *Vzorce*): winrate podle rošády, výměny dam, materiálu,
  pěšcové struktury, konce partie, délky partie a **formy po předchozí partii**;
  plus **Elo-adjusted výkonnost** a „štěstí" (z-skóre),
- **statistická rigoróznost všude, kde se ukazuje winrate**: Wilsonův interval
  spolehlivosti a empirical-Bayes shrinkage v tooltipu (ať malý vzorek nevypadá
  stejně důvěryhodně jako velký), Markovova řetězová predikce ve stromu zahájení,
  Kaplan–Meierův medián (cenzurovaná data) pro vstup do koncovky,
- **grafy** (karta *Grafy*): eval graf partie, vývoj ACPL/přesnosti/IPR (i konverze/
  záchrany/Tactical Awareness) v čase, winrate podle zahájení, histogram divokosti
  partií, kumulativní „štěstí" v čase, dotahování (konverze/záchrana sloupcově),
  radar profilu hráče, kritičnost×přesnost tahu, koláč tahů podle chess.com
  kategorie, heatmapa winrate podle dne v týdnu a hodiny, Elo hráč×soupeř scatter
  (barva podle výsledku); + histogramy se zvonovou (normální) křivkou a boxem
  Průměr/Sm. odchylka/N (styl Minitab) pro délku partie, první braní, vstup do
  koncovky, materiálové manko, Elo rozdíl soupeře, ztrátu bodů z vyhraných
  pozic a Tactical Awareness, vše přes celou databázi (+ délka partie podle
  výsledku jako tři sloupcové histogramy přes sebe),
- **filtr databáze** podle roku, tempa hry a síly soupeře – platí pro všechny rozbory hráče.

Velké databáze (tisíce partií) se načítají **na pozadí** – z každé partie se uloží
jen hlavičky a tahy, pozice a texty tahů se dopočítají teprve když jsou potřeba.

## Instalace

```bash
cd chess_app
pip install -r requirements.txt
```

Vyžaduje Python 3.10+.

### Engine (Stockfish)

Engine není součástí aplikace. Stáhni Stockfish z <https://stockfishchess.org/download/>
(pro Windows stačí rozbalit `stockfish-windows-x86-64-*.exe`) a v aplikaci nastav
cestu přes menu **Engine → Nastavit cestu k enginu…**. Cesta se uloží do
`config.json` vedle aplikace, takže ji stačí nastavit jednou.

Funguje jakýkoli engine s UCI rozhraním, ne jen Stockfish.

### Nastavení a uživatelská data

`config.json` se vytvoří sám při prvním spuštění (nebo si ho zkopíruj z
`config.example.json` a uprav). Tyhle soubory vznikají za běhu, jsou lokální
a nejsou v repu (viz `.gitignore`):

| soubor | co obsahuje |
|---|---|
| `config.json` | cesta k enginu, poslední složka, volby rozboru |
| `analysis_cache.json` | výsledky rozborů partií enginem (velké) |
| `tactics_progress.json` | postup v taktických úlohách (viděno/vyřešeno, hodnocení, opakování) |
| `player_reports.json` | uložené reporty hráčů (karta *Report*) |

`analysis_cache.json` jde smazat v menu **Engine → Smazat cache rozborů partií…**,
postup v úlohách přes **Engine → Smazat postup v taktických úlohách…**.

#### Výkon enginu (vlákna, hash) – menu **Engine → Nastavit výkon enginu…**

Appka volá engine **zvlášť na každou pozici** (ne jeden dlouhý běh přes celou
partii). Zkoušeli jsme, jestli pomůže víc vláken/větší hash tabulka – hrubý
test (1→2→4→7 vláken popořadě) vypadal na jasné zpomalení s víc vlákny, ale
kontrolní měření odhalilo, že se stroj v čase sám zpomaluje (nejspíš tepelné
throttlování), což ten trend částečně zkresluje. Směr (víc vláken nepomáhá,
možná škodí kvůli režii zapínání vláken při tak krátkých voláních) drží i po
zohlednění, ale přesné číslo neznáme jistě. Hash tabulka na rychlost
měřitelně nemá vliv. Appka proto defaultně běží na **1 vlákně** (`engine_perf.py`
má v komentáři celou historii měření) – menu zůstává k dispozici pro
experimenty s jiným enginem/HW.

Co naopak **spolehlivě pomohlo** (obojí potvrzeno prokládaným / A-B testem,
ne jen měřením před/po):

1. `game_analyzer.analyse_position` přešlo ze streamování průběžných UCI zpráv
   na jedno blokující volání (`engine.analyse()`) – konzistentně **~9 %
   rychlejší** při stejné hloubce, navíc jednodušší kód. Cena: tlačítko
   **■ Zastavit rozbor** zabere až o jednu právě počítanou pozici navíc
   (ne okamžitě), při hloubkách 10–15 v řádu jednotek vteřin.
2. **Paralelní enginy u dávkového rozboru** (karta *Přesnost*): `AccuracyBatch`
   umí rozjet `N` enginů souběžně (`config.json` → `engine_parallel`, nastavuje
   se v tom samém menu), každý řeší jinou partii. Naměřeno **2,5× rychleji
   s 5 enginy** (hloubka 10, důkladný rozbor). Návratnost klesá (profil: ~⅔
   času je čekání na engine – to paralelizuje –, ~⅓ je parsování v Pythonu pod
   GILem – to ne), takže strop je kolem 3×; ~4–6 enginů je rozumný sladký bod.
   Cache je thread-safe, výsledky se skládají zpět do původního pořadí partií.
3. **Paralelní rozbor jedné partie** (karta *Partie* → *Rozebrat partii enginem*):
   `GameAnalyzer` teď taky rozdělí pozice mezi `N` enginů (`engine_parallel`) –
   pozice jsou nezávislé, škáluje to čistě, naměřeno **~2,2× rychleji s 5 enginy**
   (hloubka 13–14). Živý rozbor pozice (MultiPV panel) paralelní není.
4. **Reálné W/D/L** (menu *Engine → Používat reálné W/D/L z enginu*, výchozí
   zapnuto): appka zapíná `UCI_ShowWDL` a šanci na výhru / očekávané body počítá
   z reálného W/D/L enginu (zná contempt, pravidlo 50 tahů, typ pozice) místo ze
   sigmoidy z centipawnů. Přesnější pro **praktickou** hru – v remízových
   pozicích cp výkyv nemění výsledek, v ostrých ano. Dopad je citelný:
   přesnost bývá o 10–20 bodů níž a volatilita vyšší než u sigmoidy, a čísla
   tím **nejsou srovnatelná s lichess**. Ovlivňuje: přesnost, EP, hrubky,
   volatilitu, dotahování, klasifikaci tahů, taktiku i „Hádej tah". ACPL
   zůstává v centipawnech. Kritičnost/ostrost/komplexita zůstávají na sigmoidě
   (top-3 linie W/D/L v cache nedrží). Přepočítá se až při novém spuštění
   rozboru; starší rozbory v cache zůstanou po sigmoidě, dokud je nepřepočítáš.

## Spuštění

```bash
python main.py
```

nebo s rovnou otevřenou partií:

```bash
python main.py sample.pgn
```

Ve Windows lze poklikat na `spustit.bat`.

## Ovládání

| Akce | Klávesa / tlačítko |
|------|--------------------|
| Další / předchozí tah | `→` / `←`  nebo `▶` / `◀` |
| Na začátek / konec partie | `Home` / `End`  nebo `⏮` / `⏭` |
| Plynulé přehrávání / pauza | `mezerník` nebo `▶ Přehrát` |
| Otočit šachovnici | `F` nebo `⟳ Otočit` |
| Skok na konkrétní tah | klik do seznamu tahů (záložka *Partie*) |
| Zahrát vlastní tah | klik na figuru + klik na cílové pole |
| Otevřít PGN | `Ctrl+O` |
| Vložit PGN z textu | menu Soubor |

Rychlost plynulého přehrávání se nastavuje posuvníkem pod šachovnicí.

Na horní liště je výběr **Hráč pro rozbor** (platí pro všechny karty) a hlavní
karty okna:

| Karta | Obsah |
|-------|-------|
| **Partie a rozbor** | šachovnice + pod-karty *Partie* (seznam tahů) a *Rozbor pozice* (strom zahájení + engine) |
| **Heatmapa** | heatmapa polí vybraného hráče |
| **Zahájení** | partie rozřazené podle zahájení (ECO) |
| **Koncovky** | partie rozřazené podle typu koncovky |
| **Vzorce** | winrate podle rošády, výměny dam, materiálu, struktury, konce a délky partie |
| **Přesnost** | přesnost / ACPL / EP / hrubky / shoda s enginem přes databázi (na pozadí, s cache) |
| **Grafy** | eval graf partie, vývoj v čase (i konverze/záchrana/Tactical Awareness), divokost partií, winrate podle zahájení, kumulativní štěstí, dotahování, radar profilu hráče, kritičnost×přesnost, koláč tahů podle chess.com kategorie, heatmapa winrate podle dne/hodiny, Elo hráč×soupeř scatter, + histogramy se zvonovou křivkou (délka, výsledek, první braní, koncovka, materiál, Elo, ztráta bodů, Tactical Awareness) přes celou DB |
| **Taktika** | taktické úlohy z hráčových partií (přehlédnuté i nalezené), motivy, obtížnost, řešení na šachovnici s tolerancí, hvězdička, opakování SM-2 |
| **Report** | uložené snímky statistik hráčů (Přesnost + Vzorce) a jejich vzájemné porovnání – tabulka metrik vedle sebe, srovnávací grafy (radar / sloupce / kategorie tahů), export do PDF |

Barvu hráče (**jako bílý / jako černý / obě barvy**) si volí *Heatmapa*,
*Zahájení*, *Vzorce*, *Přesnost* i *Rozbor pozice* samostatně (*Koncovky* vždy pro obě barvy);
při přepnutí partie se přepínače nastaví podle hráčů dané partie.

Rozbory na kartách **Heatmapa**, **Zahájení**, **Koncovky** a **Vzorce** se
nespouští automaticky po načtení PGN – klikni na **▶ Spustit rozbor** na dané
kartě. Po prvním spuštění se rozbor sám přepočítá při změně jeho voleb (barva,
druh pole apod.); tlačítko **↻ Přepočítat** ho vynutí. Změna hráče, nové PGN nebo
změna filtru rozbory zase vynulují.

### Filtr databáze

Řádek **Filtr** pod horní lištou omezuje, které partie do rozborů hráče vstupují
(strom zahájení, heatmapa, zahájení, koncovky i vzorce):

- **rok** od–do (rozsah se nastaví podle načtené databáze),
- **tempo** – *bullet / blitz / rapid / vážná / korespondenční* (odhad podle
  hlavičky `TimeControl`: základní čas + 40 × přírůstek),
- **soupeř** – *jen silnější / jen slabší / vyrovnaní (±100)* podle rozdílu Elo
  (hlavičky `WhiteElo` / `BlackElo`).

Vpravo je počet vyhovujících partií (*N z M*). **× Zrušit filtr** vrátí vše.
Partie bez potřebné hlavičky se při aktivním filtru na dané kritérium nezapočítají.

### Analýza enginem

Na kartě **Partie a rozbor → Rozbor pozice** zaškrtni **Analýza enginem**. Engine běží průběžně na aktuálně zobrazené pozici,
při každém posunu tahu se analýza restartuje. Počet zobrazených variant
(MultiPV) se nastavuje vedle zaškrtávátka (1–5). Hodnocení je z pohledu bílého
(`+1.50` = výhoda bílého, `#+3` = mat bílého ve 3).

Nejlepší tah je na šachovnici vyznačen **zelenou šipkou**, další varianty
modrými. Šipky lze vypnout zaškrtávátkem **Šipky na šachovnici**.

Vedle velké šachovnice je **svislý ukazatel hodnocení** (bílá zdola = výhoda
bílého). Když engine neběží, ukazuje hodnocení z posledního **rozboru partie**
(viz níže); jinak je prázdný.

#### Rozbor celé partie enginem

Na kartě **Partie** je tlačítko **⚙ Rozebrat partii enginem**, volba **hloubka**
(6–22) a volba **styl** (viz níže). Engine projde všechny pozice partie na
pozadí (multipv=3 vždy – jedna partie je levná, viz styl níže) a pak:

- **v seznamu tahů** barevně označí slabé (a u chess.com stylu i výjimečně
  dobré) tahy – viz níže. Tučný text ve stejné barvě, tooltip ukáže důvod.
  Pod tlačítkem je barevná legenda podle aktuálního stylu,
- do řádku pod tlačítkem vypíše **přesnost** obou stran (lichess vzorec z
  pravděpodobnosti výhry), **ACPL**, **kompozitní index (0–100)**, **ztrátu
  v očekávaných bodech**, počty nepřesností / chyb / hrubek a **volatilitu**
  partie (průměrný skok pravděpodobnosti výhry na půltah – jak moc partie „házelo") –
  tahle souhrnná řádka zůstává vždycky podle lichess metodiky (na tu je navázaný
  ACPL a kompozitní index i jinde v appce), styl níže mění jen značky u tahů,
- **svislý ukazatel** pak při procházení partie ukazuje hodnocení dané pozice,
- pod souhrnem se objeví řádek **Kritické momenty** – rozbalovací seznam
  půltahů s největším skokem pravděpodobnosti výhry (▲/▼ a o kolik p.b.,
  z pohledu strany na tahu), tlačítka ◀ ▶ skáčou mezi nimi. Rychlý způsob,
  jak najít zlomy dlouhé partie.

Tlačítko během výpočtu slouží k přerušení (**■ Zastavit rozbor**), po dokončení
k opakování (**↻ Rozebrat znovu**). Přepnutí na jinou partii rozbor zahodí.

##### Styl značení tahů: Lichess / Chess.com

Přepínač **styl** vedle hloubky mění, jak se v seznamu tahů (a v eval grafu na
kartě *Grafy*) tahy značí – bez nutnosti rozbor spouštět znovu (přepnutí jen
překreslí už spočítaný rozbor, `move_class.py`):

- **Lichess** (`??`/`?`/`?!`) – jak dosud, podle poklesu pravděpodobnosti výhry
  (5/10/15 procentních bodů).
- **Chess.com** – žebříček **Nejlepší/Výborný/Dobrý/Kniha/Nepřesnost/Chyba/Hrubka**
  podle ztráty v očekávaných bodech (prahy 0/0,02/0,05/0,10/0,20 – veřejně
  popsané v jejich [FAQ](https://support.chess.com/en/articles/8572705-how-are-moves-classified-what-is-a-blunder-or-brilliant-etc)),
  plus tři speciální značky. Na rozdíl od lichess stylu (kde jen chyby mají
  značku, "dobrý" tah mlčí) dostane u chess.com stylu značku **úplně každý tah
  mimo knihy** – i Nejlepší/Výborný/Dobrý (`✓✓`/`✓`/`·`, odstíny zelené) – stejně
  hustě, jak to dělá chess.com sám (Kniha jediná zůstává beze značky, je to
  kontext, ne kvalita tahu):
  - **`!!` Brilantní** – hráč obětoval materiál (jeho figura po tahu "visí" –
    přes **skutečně legální tahy** soupeře, ne jen geometrický dosah, aby se
    nepletly odkryté/vazbou stažené "obětiny" typu dáma kryta věží přes sloupec),
    pozice předtím nebyla už beztak vyhraná a po oběti zůstala v pořádku.
  - **`!` Skvělý tah** – kritická pozice (velký rozdíl nejlepšího a druhého
    nejlepšího tahu – "jediná dobrá volba", z multipv), hráč ji našel.
  - **`✗` Přehlédnutí** – soupeřův předchozí tah byl sám chyba/hrubka (nabídl
    příležitost) a hráč na ni nezareagoval o nic líp.

  Chess.com svůj přesný algoritmus pro Brilliant/Great/Miss nezveřejnil – tohle
  jsou **vlastní prahy a heuristika** podle jejich slovního popisu (číselný
  žebříček Best–Blunder ale odpovídá jejich zveřejněným hodnotám). Volba se
  pamatuje (`config.json` → `ga_style`).

#### Přehrání varianty enginu

Pod výpisem linií je **malá šachovnice**. Klikni na některou linii v seznamu a
její variantu si přehraj tah po tahu tlačítky `⏮ ◀ ▶ ⏭` pod malou šachovnicí.
Zatímco engine počítá hlouběji, přehrávaná varianta se živě prodlužuje (pozice
v přehrávání zůstává zachovaná). Malá šachovnice se otáčí spolu s hlavní (`F`).

## Rozbor hráče (funguje nad databází více partií)

Na horní liště vyber **hráče**. Barvu (**jako bílý / jako černý / obě barvy**) si
volí *Strom zahájení*, *Heatmapa* i *Zahájení* samostatně (při přepnutí partie se
nastaví podle hráčů dané partie); *Koncovky* se počítají vždy pro obě barvy.

### Strom zahájení (záložka *Rozbor pozice*)

Strom se **odvíjí od aktuální pozice na velké šachovnici** – ukazuje, jak z ní
partie vybraného hráče v databázi pokračovaly (včetně transpozic). U každého
tahu je:

- **počet partií**, které tudy prošly,
- **winrate** = výhry ÷ rozhodnuté partie z pohledu hráče (remízy se do čitatele
  nepočítají), obarvené od červené (0 %) přes žlutou po zelenou (100 %),
- **Výsledek V/R/P** jako skládaný pruh s podílem výher (zelená), remíz (šedá) a
  proher (červená); přesná čísla ukáže tooltip,
- podíl na pozici rodiče.

Hloubka stromu je nastavitelná. Zobrazí se jen první tahy z pozice; další tahy
varianty se dopočítají **až po rozbalení** řádku. **Dvojklik na tah** ho (i
s celou cestou k němu) přehraje na velké šachovnici jako analýzu – strom se pak
odvíjí od nové pozice.

### Heatmapa (karta *Heatmapa*)

8×8 mřížka polí s počtem tahů na dané pole, barevnou škálou (krémová → oranžová →
cihlová), legendou a orientací přes *Pohled černého*. Volby:

- **Čí tahy** – *hráče* / *soupeře* (tahy soupeřů v partiích vybraného hráče),
- **Pole** – *kam táhne* / *odkud táhne* / *jen braní (kam)*,
- **Figura** – filtr na druh figury,
- **Fáze** – *celá partie* / *zahájení (1–15)* / *střední hra (16–40)* / *koncovka (41+)*,
- **Partie** – *všechny* / *jen výhry* / *jen prohry* (podle výsledku hráče).

Kombinací voleb jde srovnat třeba „kam stavím jezdce ve výhrách vs prohrách" nebo
„kde soupeři berou v zahájení". Platí filtr databáze.

### Vzorce (karta *Vzorce*)

Partie vybraného hráče (vlastní přepínač **jako bílý / jako černý / obě barvy**)
rozdělené podle řady kritérií; u každého koše je počet partií, **winrate**
(výhry ÷ rozhodnuté, remízy se do čitatele nepočítají) a pruh **V / R / P**.
Rozbor se spustí až tlačítkem, platí filtr databáze. Skupiny:

- **Rošáda** – strana rošády hráče (O-O / O-O-O / bez), vzájemný vztah rošád
  (stejná / opačná strana / jeden nerošoval / …), načasování rošády hráče.
- **Výměna dam / těžké figury** – kdy zmizely obě dámy (koše po tazích),
  partie s dámami vs. bez dam.
- **Materiál a výměny** – kdy padla první figura/pěšec, tempo výměn do 20. tahu
  (kolik figur zmizelo), největší **usazené** materiálové manko a náskok hráče
  (úroveň, která přežila i soupeřovu odpověď – odfiltruje běžná braní–zpětná
  braní, nechá skutečné oběti a zisky), zda měl hráč dvojici střelců.
- **Pěšcová struktura** (snímek pozice kolem 20. tahu) – izolovaný pěšec / IQP,
  zdvojení pěšci, volný pěšec (čí), typ centra (otevřené / pootevřené / zavřené
  podle pěšců na sloupcích d a e), **pěšcové ostrovy** hráče (počet souvislých
  skupin na sousedních sloupcích – míň je líp), **napětí** ve struktuře (kolik
  dvojic pěšců se navzájem může vzít – 0 = vyjasněná), **zpátečnický pěšec**.
- **Král a útok** – jak partie skončila (mat / vzdání nebo čas / remíza dohodou /
  remíza pravidlem / pat), zda hráčův král zůstal v centru (nerošoval a partie
  měla 20+ tahů), **pěšcová bouře** na soupeřova krále (3+ pěšcových tahů hráče
  na křídle, kam se soupeř uklidil, do 25. tahu).
- **Délka a průběh partie** – délka partie v koších po tazích; podíl rozhodnutých
  vs. remízovaných partií.
- **Forma a kontext** – winrate podle výsledku **bezprostředně předchozí partie**
  ve stejné „seanci" (mezera < 60 minut, podle časového razítka v hlavičce) –
  po výhře / po remíze / po prohře. Ukazuje tilt (horší forma po prohře) nebo
  naopak.

Pod stromem je navíc volně-textová sekce **„Elo-adjusted výkonnost a štěstí"**
(z hlaviček, bez enginu):

- **Elo-adjusted výkonnost** – skutečné skóre minus to, co by čekal rozdíl v Elu
  soupeřů (`E = 1/(1+10^((Elo_soupeře−Elo_hráče)/400))`) – silnější signál než
  syrový winrate, protože odfiltruje „vyhrávám hlavně proti slabším",
- **„štěstí"** – z-skóre skutečného skóre vs. Elo-očekávaného; blízko 0 =
  výkon odpovídá síle soupeřů, vysoké |z| = neobvykle šťastná/nešťastná série
  (nebo skutečná změna formy – u velkého vzorku partií vyjde i malý efekt
  „signifikantní", posuď v kontextu).

#### 🔍 Nejpodivnější partie

Tlačítko vpravo nahoře (`anomaly.py`). **Detekce odlehlých partií** – které jsou
nejméně podobné tvé běžné hře, se zaměřením na partie **zajímavé ke studiu**.
Postup:

0. **Filtr** – nejdřív vyřadí partie, co nemohly být napínavé: mimo tvé Elo
   pásmo (|Δ| > 350), rozhodnuté drtivou trvalou materiální převahou (> věž –
   „soupeř s holým králem tahá do matu" není k ničemu), miniaturky (< 12 tahů),
   nedohrané.
1. Zbytek popíše **~24 číselnými vlastnostmi** (jen z hlaviček + jednoho
   průchodu tahy, bez enginu): délka, načasování rošády / výměny dam / prvního
   braní, oběť / trvalé manko (oříznuto na 6), pěšcová struktura (izolák,
   ostrovy, napětí, zdvojení, volný pěšec…), typ centra, opačné rošády, král
   v centru, typ konce (mat / vzdání).
2. Každý sloupec projde **van der Waerdenovou transformací** (pořadí → normální
   kvantil), takže je marginálně ~N(0,1) bez ohledu na tvar (odfiltruje šikmé
   počty a vzácné binární vlastnosti, co by jinak rozhodily výpočet).
3. **Kovarianční matice se shrinkage** k jednotkové (aby šla invertovat i při
   kolinearitě, např. IQP ⇒ izolák) a spočítá se **Mahalanobisova vzdálenost**
   d² = zᵀ Σ⁻¹ z. Na rozdíl od „daleko od průměru v jedné ose" tohle bere
   v úvahu i to, které kombinace vlastností jsou u tebe neobvyklé.
4. **Reweighting** – d² se přepočítá jen z 90 % nejméně odlehlých partií, ať
   samy odlehlé partie nenafouknou Σ a „neschovají se"; medián se pak přeškáluje
   na teoretický medián χ²(25), aby „divnost %" seděla.
5. **Divnost %** = χ² percentil d² (Wilsonova–Hilfertyho aproximace). U každé
   partie se d² **rozloží na příspěvky jednotlivých vlastností** (zᵢ·(Σ⁻¹z)ᵢ) →
   sloupec „proč" ukáže 3 vlastnosti, co partii dělají nejvíc odlišnou (hodnota
   partie vs. tvůj medián).

Odlehlé partie bývají nejpoučnější – pozice **nepodobné tvé běžné hře**
(uzamčené struktury s pěšcovým napětím, neobvyklé načasování výměny dam,
roztříštěná pěšcová struktura, pozdní rošáda, král v centru, opačné rošády,
dlouhé manévrovací partie) a člověk si je sám nevybere. Dvojklik partii otevře.

### Přesnost (karta *Přesnost*)

Engine projde partie vybraného hráče (vlastní přepínač barvy, **od nejnovějších**,
počet omezí *max partií* – bez horního stropu, nastav vysoko = všechny; rozbor
je ale pomalý, u tisíců partií počítej v desítkách minut i s víc enginy) do
zadané **hloubky** a spočítá:

- **Přesnost %** – lichess vzorec: hodnocení → pravděpodobnost výhry → přesnost
  tahu `103,17·e^(−0,04354·Δwin%) − 3,17`; přesnost partie = průměr váženého
  (volatilitou pozice) a harmonického průměru přesností tahů,
- **ACPL** – průměrná ztráta v setinách pěšce oproti nejlepšímu tahu,
- **EP ztráta / partii** – kolik *očekávaných bodů* (0–1) hráč za partii prohospodařil,
- **Hrubky / 100 tahů** – klasifikace podle poklesu pravděpodobnosti výhry
  (`?!` ≥ 5, `?` ≥ 10, `??` ≥ 15 p.b., jako lichess); tooltip má i `?` a `?!`,
- **Shoda T1 %** – jak často hráč zahrál engine-nejlepší tah (po konci teorie
  podle ECO a bez vynucených tahů),
- **Index** – kompozitní index přesnosti 0–100 (45 % přesnost, 30 % ACPL,
  15 % chybovost, 10 % T1; vlastní, ne CAPS),
- **IPR** – odhad výkonnosti. Ukotvený na hráčovo **průměrné Elo v rozboru**;
  per-skupinu měří, o kolik hrál nad/pod svou úroveň podle kvality tahů
  (ln ACPL). Sklon je pevný (−580 Elo/ln), nebo se dofituje, když Elo v rozboru
  dost kolísá. Souhrn ≈ tvé Elo (z definice) – vypovídající jsou **řádky po barvě
  a období** a **poslední partie** (forma). Bez hlaviček s Elem se použije hrubá
  křivka `Elo ≈ 3940 − 580·ln(ACPL)`. Jedna partie hodně kolísá (odchylka od
  kotvy omezená na ±450).

Rozpad: **souhrn**, **podle barvy**, **podle fáze** (zahájení podle ECO / středhra /
koncovka – detektor koncovky sdílený s kartou *Koncovky*), **podle tempa**,
**vývoj v čase** (po letech, nebo po měsících, když rozbor nepokrývá 2+ roky).
Dole je sbalený seznam **jednotlivých partií** (dvojklik partii otevře) – vidíš,
které partie táhnou průměr dolů; má stejné sloupce jako souhrn, včetně EP ztráty
a T1 % za tu jednu partii.

Pod sekcemi *Charakter partií* / *Dotahování* je sbalený seznam **Brilantní tahy
hráče** – brilantní tahy hráče (oběť materiálu ve zdravé pozici) z rozboru,
s číslem tahu; **dvojklik skočí na ten tah v partii**.

Tabulka má navíc sloupce **Volatilita %**, **Obraty**, **Ostrost**, **Komplex.**
a **Takt. %** – u každé partie i u souhrnných řádků (po barvě / tempu / období).
Všechno jsou to **per-pozici** veličiny shrnuté na jedno číslo za partii:
*volatilita* = průměrná změna pravděpodobnosti výhry na jeden půltah, *obraty* =
kolikrát za partii se prohodilo, kdo stojí líp, *ostrost* / *komplexita* =
průměr přes pozice hráče na tahu (viz níže, potřebují „důkladný rozbor"),
*Takt. %* = poměr trefených engine-tahů v pozicích s jasně nejlepším tahem
(kritičnost ≥ 0,15). Řádky *podle fáze* mají u volatility/obratů/ostrosti/
komplexity pomlčku – to jsou celopartiové veličiny, do fází se nedělí.

#### Charakter partií a dotahování

Dvě další sekce, spočítané ze stejného rozboru (bez enginu navíc):

- **Charakter partií** – **volatilita** (průměrný skok pravděpodobnosti výhry na
  půltah – jak moc partie „házelo"; kolik obratů, kdo stojí líp; kolik partií je
  „divokých"), **medián vstupu do koncovky** (Kaplan–Meierův odhad – partie, co
  skončí dřív, aniž koncovky dosáhly, se počítají korektně jako *cenzurované*
  (aspoň tolik tahů to vydrželo), ne jako by koncovky nikdy nedošly; robustnější
  než prostý průměr). Se zaškrtnutým **„důkladný rozbor"** (viz níže) navíc **ostrost**
  pozic (0 = skoro každý tah drží, →1 = drží jediný), **komplexita** (rozptyl
  hodnocení nejlepších tahů – jak snadné bylo se seknout) a **kritičností vážený
  ACPL** – stejný ACPL, ale chyby v lhostejných pozicích váží málo a chyby
  v pozicích, kde na tahu skutečně záleželo, váží hodně; bývá výrazně nižší než
  syrový ACPL. Se stejným „důkladným rozborem" navíc **Tactical Awareness** –
  % shody s enginem, ale jen v tazích s kritičností ≥ 0,15 (tj. tam, kde byl
  jasně nejlepší tah citelně lepší než druhý nejlepší) – narozdíl od T1 (shoda
  ve všech nekniha/nenucených tazích) tak neředí číslo klidnými pozicemi, kde
  je „skoro cokoli stejně dobré". Má i histogram na kartě *Grafy*.
- **Dotahování** – **conversion rate**: z partií, kde měl hráč jasně vyhranou
  pozici (pravděpodobnost výhry ≥ 75 % aspoň 3 tahy po sobě), kolik doopravdy
  vyhrál, a kolik bodů v průměru z dosaženého maxima ztratil. **Záchrany**:
  z partií s jasně prohranou pozicí (≤ 25 %), kolik neskončilo prohrou. Oboje
  má i graf na kartě *Grafy* (sloupcový + histogram ztráty bodů).

Zaškrtávátko **„důkladný rozbor"** zapne u každé pozice `multipv = 3` (nejlepší
tah + druhý a třetí nejlepší) – potřeba pro ostrost/komplexitu/kritičnost a pro
přesnější shodu T1. Přibližně **2× pomalejší** než základní rozbor; dá se
zapnout i dodatečně, cache si k partiím, které to ještě nemají, tuto informaci
při dalším spuštění dopočítá (partie, co ji už mají, se přeskočí).

Výsledky rozborů partií se ukládají do **`analysis_cache.json`** vedle aplikace
(klíč = hlavičky + tahy, s hloubkou; navíc WDL a top-3 tahy, když je k dispozici),
takže opětovné spuštění je okamžité a cache sdílí i rozbor jedné partie na kartě
*Partie*. Běží na pozadí, jde přerušit. Orientační rychlost: ~1 s/partie při
hloubce 10 (základní rozbor), ~2–3 s/partie při hloubce 12 s „důkladným rozborem".
Vymazat ji jde v menu **Engine → Smazat cache rozborů partií…** (ptá se na
potvrzení a ukáže, kolik partií smaže; rozbory se pak musí spočítat znovu).
Vedle je **Engine → Smazat postup v taktických úlohách…**, který maže jen
`tactics_progress.json` (co jsi viděl/vyřešil, hodnocení, hvězdičky, plán
opakování) – cache rozborů i úlohy samotné zůstanou.

### Zahájení (karta *Zahájení*)

Partie hráče rozřazené podle **zahájení**; vlastní přepínač **jako bílý / jako
černý / obě barvy**. Název zahájení se bere v pořadí: hlavička `[Opening]` /
`[Variation]` → **plná databáze zahájení ECO** (lichess `chess-openings`,
~3800 variant, soubor `eco.tsv` – nejdelší shoda úvodních tahů) → podrobný název
z `[ECOUrl]` chess.com (přeložený na běžné české názvy).

Strom má tři úrovně: **ECO kód → varianta → jednotlivé partie**; po spuštění je
**sbalený**. U kódu i varianty je počet partií, winrate a pruh V/R/P (u obou
s **Wilsonovým intervalem spolehlivosti** a **staženým odhadem** v tooltipu –
viz níže). **Kliknutím na hlavičku sloupce** se strom seřadí (abecedně / podle
počtu partií / podle winrate); řazení se drží i po přepočítání. **Dvojklik na
partii** ji otevře na kartě *Partie a rozbor* od konce teoretické linie zahájení.

Nad stromem je navíc:

- **diverzita repertoáru** – Shannonova entropie převedená na „efektivní počet
  zahájení" (`2^H`, resp. `1/Σp²`), zvlášť na úrovni ECO rodiny a konkrétní
  varianty, + kolik partií pokrývají tři nejhranější zahájení,
- **hloubka teorie / book exit** – dvě čísla: podle **jmenované databáze**
  (nejdelší shoda v `eco.tsv`) a podle **vlastní historie hráče** (kolikátým
  tahem partie poprvé opustí pozici, kterou hráč nikde jinde v databázi
  nehrál – transpozice se počítají, žádná externí databáze ani engine
  netřeba). Rozdíl mezi nimi ukazuje, jestli hráč jde za katalogizovanou
  teorii (vlastní příprava), nebo naopak i „knihovní" tahy nemá zažité.

### Wilsonův interval a shrinkage (tooltip u winrate)

Kdekoli appka ukazuje **winrate** (Vzorce, Zahájení, Koncovky, strom zahájení),
tooltip nad procentem nese:

- **Wilsonův 95% interval spolehlivosti** – u 5 partií je široký (nedůvěřuj
  číslu), u 500 úzký (spolehlivé),
- **stažený (empirical-Bayes) odhad** – winrate koše stažený k celkovému
  průměru hráče silou, kterou appka odhadne z rozptylu mezi koši (DerSimonian–
  Laird styl, váženo počtem partií v koši) – málo věrohodný koš (100 % ze
  3 partií) se stáhne blízko průměru, opravdu odlišný koš (spolehlivě jiný na
  velkém vzorku) zůstane skoro nezměněný. Ve stromu zahájení navíc uzly bez
  vlastních dat dostanou i predikci z podstromu (empirický Markovův řetězec –
  vážený průměr predikcí dětí).

### Koncovky (karta *Koncovky*)

Partie vybraného hráče (**obě barvy dohromady**) jsou rozřazené podle **typu
koncovky**. Jelikož jedna koncovka přechází v druhou, jedna partie může spadat
pod více kategorií.

**Koncovka** = pozice, kde má každá strana ≤ 13 bodů materiálu bez krále
(Speelman: D=9, V=5, S/J=3, P=1) **nebo** jsou na šachovnici ≤ 4 figury mimo krále
a pěšce (Minev). Počítají se jen pozice, kde je **rozdíl v materiálu nejvýše
4 body** – **výjimkou jsou pěšcové koncovky** (jen král a pěšci), které se
počítají vždy, bez omezení na počet pěšců i na rozdíl materiálu.

Kategorie = **pojmenovaný typ koncovky** (podle druhů figur na šachovnici)
**+ přesný soupis figur obou stran** (kolik věží / dam / střelců / jezdců),
nezávislý na barvě hráče i na počtu pěšců – silnější strana (podle hodnoty figur)
je v zápisu první, takže stejné složení figur dá vždy jen jednu kategorii:

- *Věžová koncovka: V vs V* × *2V vs V*, *Jezdcová koncovka: 2J vs J*,
- *Střelcová koncovka – střelci stejné / opačné barvy*, *Střelec proti jezdci*,
- *Věž a střelec: V+S vs V* × *V+S vs V+S*, *Věž a jezdec: V+J vs V+J*,
  *Věž a lehké figury: V+S vs V+J*,
- *Dáma a jezdec: D vs J*, *Těžké figury (dáma a věž): D+V vs V*, …

Strom je po spuštění **sbalený** a **kliknutím na hlavičku sloupce** se dá seřadit
(abecedně / podle počtu partií / podle winrate). Strom má tři úrovně: **kategorie →
počet pěšců na šachovnici → jednotlivé partie**. Pěšcovky
jsou pod jednou kategorií *Pěšcová koncovka* s podřádky *1 pěšec / 2 pěšci / …*;
stejné dělení má i každá další kategorie. Každá partie je v kategorii započítaná
právě jednou, podle počtu pěšců při vstupu (součet podřádků = počet partií
kategorie). Na každé úrovni je počet partií, winrate a pruh V/R/P. **Dvojklik na partii** přepne na kartu
*Partie a rozbor* a nastaví šachovnici na tah, kterým se partie stala danou
koncovkou (u pěšcovek na chvíli, kdy měla daný počet pěšců).

### Grafy (karta *Grafy*)

Devatenáct grafů, přepínač **Graf:** nahoře; podle typu se objeví ještě **Ukazatel**
(jen u vývoje v čase) nebo **barva**. **↻ Překreslit** nebo přepnutí na kartu
graf obnoví.

**Z rozboru jedné partie / karty Přesnost** (`PySide6.QtCharts`):

- **Eval graf vybrané partie** – hodnocení (% bílého) přes celou partii z pohledu
  aktuálně vybrané partie na kartě *Partie*, s barevnými tečkami na `??`/`?`/`?!`
  tazích. Potřebuje partii rozebranou enginem (⚙ na kartě *Partie*).
- **Vývoj v čase** – spojnicový graf ACPL / přesnosti / IPR / **konverze / záchrany /
  Tactical Awareness**, z posledního rozboru na kartě *Přesnost* (nepočítá nic
  navíc, jen kreslí sekci „Vývoj v čase"). Granularita se volí automaticky:
  **po letech**, pokud rozbor pokrývá aspoň 2 různé roky, jinak **po měsících**
  (stačí aspoň 2 různé měsíce) – vyžadovat vždy 2 celé roky nedávalo smysl pro
  někoho, kdo hraje intenzivně jen pár měsíců. Úplně prázdný je graf jen tehdy,
  když rozbor nemá partie z aspoň 2 různých měsíců.
- **Divokost partií** – histogram volatility (viz karta *Přesnost* → Charakter
  partií) přes partie z posledního rozboru na kartě *Přesnost*.
- **Winrate podle zahájení** – vodorovný sloupcový graf, top 15 zahájení podle
  počtu partií, seřazeno podle winrate. Přesný Wilsonův interval pro každé
  zahájení je v tooltipu na kartě *Zahájení*.

- **Kumulativní „štěstí"** – běžící součet (skutečné − Elo-očekávané skóre)
  partie po partii, chronologicky, jen partie se známým Elem soupeře
  (`patterns.game_log()`, přes celou databázi, bez enginu).
- **Dotahování: konverze / záchrana** – sloupcový graf, dva sloupce vedle sebe:
  „Vyhrané pozice → dotaženo" a „Prohrané pozice → zachráněno" (v %). Stejná
  čísla jako v textu sekce *Dotahování* na kartě *Přesnost*, jen jako graf.
- **Ztráta bodů z vyhraných pozic** (histogram, Minitab styl) – kolik bodů
  hráč v průměru ztratí z dosaženého maxima v partiích, kde měl jasně vyhráno
  (`ep_wasted`, 0 = dotaženo beze ztráty, blízko 1 = vyhraná partie zahozena).
- **Tactical Awareness** (histogram, Minitab styl) – nové číslo: shoda s enginem
  jen v pozicích, kde měl hráč **jasně nejlepší tah** (kritičnost ≥ 0,15 EP
  jednotky – viz *Charakter partií* → kritičností vážený ACPL, stejná veličina).
  Na rozdíl od běžného T1 (shoda ve všech nekniha/nenucených pozicích) tak měří
  přímo „když na tahu záleželo, viděl to?". Potřebuje **„důkladný rozbor"**
  (multipv) – bez něj se kritičnost nepočítá a graf i souhrnné číslo zůstanou
  prázdné.
- **Profil hráče (radar)** – paprskový graf (`QPolarChart`), 6 os normalizovaných
  na 0–100: Přesnost, ACPL (invertovaně: `100 − ACPL/3`), T1 %, Tactical
  Awareness, Konverze, Záchrana. Osa se vynechá, pokud pro ni rozbor nemá data
  (např. Tactical Awareness bez „důkladného rozboru", Záchrana bez jediné
  prohrané pozice) – potřeba aspoň 3 naplněné osy.
- **Kritičnost × přesnost tahu** – spojnicový graf: tahy z „důkladného rozboru"
  rozdělené do 10 košů podle kritičnosti pozice (0 = klid, 1 = jasně nejlepší
  tah), na ose Y průměrná přesnost tahu v tom koši. Klesající křivka = hráč
  chybuje víc v ostřejších pozicích. (Skutečný bodový scatter přes statisíce
  tahů celé databáze by byl nečitelný shluk, proto agregace po koších –
  vypovídá o stejné otázce: chybuje hráč víc, když je pozice ostrá?)
- **Přesnost / hrubky podle figury a typu tahu** – sloupcový graf: průměrná
  přesnost tahu (resp. hrubky na 100 tahů) hráče rozdělená podle **tažené
  figury** (pěšec / jezdec / střelec / věž / dáma / král) a vedle podle **typu
  tahu** (braní / tichý tah / šach / rošáda / proměna). Odhalí systematickou
  slabinu – např. „s dámou dělám 2× víc hrubek než s ostatními figurami" nebo
  „spěšné šachy mě stojí body". Jen tahy hráče, z rozboru na kartě *Přesnost*;
  na kartě *Report* jde porovnat mezi hráči.
- **Chybová heatmapa** – heatmapa polí na šachovnici: na kterých polích stály
  tvoje figury, když jsi udělal chybu (nepřesnost/chyba/hrubka), varianta
  „odkud táhnu" a „kam táhnu". Sjednoceno na **perspektivu hráče** (tvá 1. řada
  dole, tahy za černého zrcadleny), takže „e4" nemíchá bílého s černým.
  Poznámka pod grafem vypíše nejchybovější pole (podíl chyb ze všech tahů z něj).
- **Tahy podle kategorie (chess.com), koláč – 1 partie** – rozložení tahů
  aktuálně rozebrané partie (karta *Partie*) do všech 10 chess.com kategorií
  (i Kniha a "tiché" Nejlepší/Výborný/Dobrý – na rozdíl od seznamu tahů, kde
  tyhle mlčí jen v lichess stylu, teď v obou stylech svítí i tady). Obě strany
  dohromady; nezávisí na tom, jaký styl (lichess/chess.com) je zrovna zvolený
  v seznamu tahů – rozbor multipv=3 se počítá vždy (`game_analyzer.py`'s
  `cc_counts`).
- **Tahy podle kategorie (chess.com), koláč – celý rozbor** – totéž, ale
  sečtené přes všechny partie z posledního rozboru na kartě *Přesnost*
  (`accuracy_batch.py`'s `cc_counts`, žádný nový enginový průchod – jen tally
  navíc uvnitř rozboru, co se stejně počítá). Skvělý tah se objeví jen se
  zapnutým „důkladným rozborem" (multipv), stejně jako jinde. **V legendě**
  nahoře je **počet tahů** v každé kategorii, **na výsečích** jejich **podíl
  v procentech**.

**Histogramy přes celou databázi** (styl Minitab – sloupce četností + vyrovnávací
normální křivka + box Průměr / Sm. odchylka / N vpravo nahoře; vlastní `QPainter`
widget `histogram_widget.py`, protože potřebuje kombinovat sloupce, spojitou
křivku a textový box na jedné ploše):

- **Délka partie** – histogram počtu tahů (koš 5 tahů).
- **První braní** – histogram tahu prvního braní (koš 2 tahy); partie bez
  jediného braní do histogramu nejdou (počet je v popisku pod grafem).
- **Vstup do koncovky** – histogram tahu, kdy partie poprvé splnila definici
  koncovky (viz karta *Koncovky*, koš 5 tahů); partie, co tam nedojdou, do
  histogramu nejdou.
- **Materiálové manko** – histogram „usazeného" materiálového manka hráče
  v bodech (koš 1 bod; 0 = partie, kde hráč nikdy nebyl pod materiálem) –
  stejná definice jako ve *Vzorcích* (úroveň, která přežila i soupeřovu
  odpověď, odfiltruje běžné braní–zpětné braní).
- **Elo rozdíl soupeře** – histogram (hráč − soupeř, koš 50); partie bez Ela
  soupeře do histogramu nejdou.

Šířka koše se automaticky zdvojnásobuje, dokud by histogram neměl přes 40 sloupců
(jeden odlehlý bod jinak natáhne osu a zbytek sloupců zmáčkne k sobě). Těchto pět
stojí na `patterns.distribution_stats()` – lehký průchod přes všechny (filtrované)
partie hráče, cachovaný podle hráče/barvy/filtru (`MainWindow._get_distribution`),
ať přepínání mezi nimi nepočítá pořád dokola.

Mimo Minitab styl (grafy potřebují tři skupiny vedle sebe, ne jednu vyrovnávací
křivku) je na stejných datech ještě **Délka partie podle výsledku** – tři
sloupcové histogramy délky partie (koš 15 tahů) přes sebe, zvlášť pro výhry /
remízy / prohry (`QtCharts`, obyčejné seskupené sloupce) – ukazuje, jestli hráč
prohrává spíš v krátkých, nebo dlouhých partiích.

**Další grafy přes celou databázi:**

- **Heatmapa winrate podle dne a hodiny** – mřížka 7 dny × 12 dvouhodinových
  bloků. Kvůli čitelnosti: winrate se do buňky s aspoň 3 partiemi **vypisuje
  číslem** (barva je jen doplněk), malé číslo pod ním = počet partií, „·N" =
  míň než 3 partie. Barevná škála je **diverging kolem hráčova celkového
  winrate** (ne kolem 50 %) a plné barvy dosáhne už při ±15 p.b., takže i běžné
  hodnoty mají vidět barvu. Vpravo a dole jsou **okrajové součty** (winrate za
  celý den / za blok hodin) – ty jsou nejčitelnější. Vlastní `QPainter` widget
  (`time_heatmap_widget.py`), data z `patterns.game_log()`. Čas je z `UTCTime`
  v PGN hlavičce převedený na **místní čas ČR** (CET/CEST, včetně letního času –
  `patterns.to_prague_local()`, pevné evropské pravidlo, bez závislosti na
  `tzdata`). Předpokládá, že hráč hraje z ČR.
- **Elo hráče × Elo soupeře (scatter)** – bod za partii, barva podle výsledku
  (zelená výhra / šedá remíza / červená prohra), přerušovaná čára = stejné
  Elo obou stran. Tečky jsou poloprůhledné (síla průhlednosti se škáluje podle
  počtu partií, stejná myšlenka jako dřív u spaghetti grafů) – překryv nad
  čárou/pod ní tak ukazuje hustotu, ne jen jednotlivé body. Data z nové
  `patterns.rating_pairs()`.

### Taktika (karta *Taktika*)

Taktické úlohy **vytažené z hráčových rozebraných partií** (`tactics.py`) – žádný
engine navíc, jen se čte, co je v `analysis_cache.json` (ideálně z „důkladného
rozboru", kvůli top-3 tahům). Úloha = pozice, kde strana na tahu měla **silný
forsírující tah** (braní / šach / proměna) s velkým dopadem na šanci na výhru,
a hráč / soupeř ho buď:

- **přehlédl** – zahrál něco výrazně horšího (rozdíl ≥ 20 p.b. šance na výhru), nebo
- **našel** – zahrál ten nejlepší a hodně tím získal / byla to jediná dobrá volba.

Ke každé úloze se určí (bez enginu, `motifs.py`):

- **taktický motiv** – mat, mat na první řadě, vidlička, dvojitý útok, vazba,
  napíchnutí, objevený útok, dvojšach, proměna, oběť (co jde spolehlivě poznat
  z pozice; odlákání a spol. ne),
- **obtížnost** (odhad Elo) – z Ela hráče + přirážky za tichý tah / oběť / počet
  možností / malý dopad,
- **tolerované řešení** – jiný, taky forsírující tah do 3 p.b. od nejlepšího
  (z top-3 „důkladného rozboru") se uzná taky.

**Filtry:** za hráče (barva) · čí tah (hráč / soupeř / oba) · typ (přehlédnuté /
nalezené / vše) · stav · **jen ★** · **motiv** · **pásmo obtížnosti** ·
**jen k opakování dnes**.

**Řešení:** vyber úlohu → zahraješ tah na šachovnici. **💡 Nápověda** prozradí
motiv. Shoda s (tolerovaným) nejlepším tahem = *správně*, jinak *špatně*
(počítá se první pokus, pak jde zkoušet dál nebo *Ukázat řešení*).
Ke každé úloze je **hodnocení užitečnosti** (👍/👎) a **★ pěkná úloha**.

**Opakování (SM-2):** po vyřešení se úloha naplánuje na příště podle toho, jak
šla (hned správně → delší interval; špatně → zpět do dnešní fronty). **▶ Trénink
opakování** zapne filtr *jen k opakování dnes* a projíždíš úlohy, co „dozrály";
**Další ▶** skáče na další k opakování. Souhrn ukazuje „⏰ N k opakování dnes".

Vše (viděl / vyřešil / hodnocení / hvězdička / plán opakování) se ukládá do
**`tactics_progress.json`** vedle aplikace, klíč = hash(partie + tah).

Hledání úloh běží **na pozadí** (`TacticsExtractor`) – u tisíců rozebraných
partií je to práce na desítky sekund. Přenačte se po rozboru na kartě *Přesnost*
nebo ručně **↻ Najít úlohy**. Dvojklik na řádek otevře partii na tom tahu.

### Hádej tah (tlačítko *🎓 Hádej tah* na kartě *Partie*)

Přehraješ **rozebranou** partii se skrytým enginem a u každého svého tahu tipuješ,
jaký bys zahrál (`guess_move.py`). Tvůj tip se ohodnotí **přesností** (lichess
vzorec) proti nejlepšímu tahu enginu – tip, který je v top-3 rozboru, se ohodnotí
okamžitě z cache, jinak si dialog pozici po tvém tahu nechá dopočítat (jedno
volání enginu). Vidíš svůj tah, přesnost, engine-nejlepší a co padlo v partii;
na konci průměrná přesnost tvých tipů, shoda s enginem a srovnání s tvou
skutečnou přesností v té partii. Vybíráš barvu a od kolikátého tahu začít.

### Report (karta *Report*)

Uložené **snímky spočítaných statistik** hráče a jejich **vzájemné porovnání**
(`player_report.py`, `report_export.py`).

**Uložení:** tlačítko **💾 Uložit report aktuálního hráče** vezme aktuální
výsledek rozboru z karty *Přesnost* (musí být spuštěný – jinak se nabídne uložit
jen část z *Vzorců*) a **na pozadí** dopočítá statistiky z karty *Vzorce* plus
podklady pro všechny grafy (rozdělení přes celou DB, chronologický deník partií,
zahájení, Elo dvojice). U velké databáze to trvá i minutu, appka mezitím běží
dál. Snímek obsahuje: přesnost/ACPL/EP/hrubky/T1/IPR/index, charakter partií
(i per-partii), dotahování, kategorie tahů chess.com, brilantní tahy,
kritičnost×přesnost; z *Vzorců* winrate po koších + Elo-adjusted převahu a
„štěstí"; a data pro histogramy (délka, první braní, koncovka, materiál, Elo).
Ukládá se do **`player_reports.json`** vedle aplikace (i s aktivním filtrem
databáze a datem).

Uložené reporty jde **Přejmenovat** a **🗑 Smazat report** (tlačítka pod seznamem;
smazání se ptá na potvrzení a je nevratné – maže z `player_reports.json`).

**Porovnání:** vlevo vyber 2+ uložené reporty (Ctrl+klik) a **Porovnat vybrané**.
Zobrazí se **tabulka metrik vedle sebe** – nejlepší hodnota v řádku je zeleně
(volatilita, ostrost, komplexita a „štěstí" se nehodnotí, jen zobrazují) – a
**srovnávací graf** (přepínač): stejná sada jako na kartě *Grafy*, jen s jednou
sérií na hráče – profil (radar), vývoj v čase, kumulativní štěstí, konverze/
záchrana, kritičnost×přesnost, kategorie tahů chess.com, Elo hráč×soupeř, a
překryté frekvenční křivky pro divokost / Tactical Awareness / délku partie /
první braní / vstup do koncovky / materiálové manko / rozdíl Ela; heatmapa
dne×hodiny a winrate podle zahájení se kreslí za každého hráče zvlášť.
(Když mají porovnávané reporty různou granularitu „vývoje v čase" – jeden po
měsících, druhý po letech – graf je sjednotí na roky, ať jsou body zarovnané.)

**Export do PDF:** tlačítko **⬇ Export do PDF (všechny grafy)** uloží porovnání –
tabulka metrik + **všechny** srovnávací grafy jako obrázky + textová shrnutí za
každého hráče – jako samostatný A4 soubor k poslání nebo tisku (`QTextDocument`
→ `QPrinter`).

Reporty uložené starší verzí appky nemají podklady pro histogramy/heatmapu –
u porovnání to appka oznámí, stačí je uložit znovu.

### Vlastní tahy (analýza na šachovnici)

Z jakékoli pozice můžeš klikáním na šachovnici zahrát vlastní tahy a zkoumat
varianty (engine je rovnou analyzuje). Klikni na figuru – zvýrazní se možné
tahy – a pak na cílové pole. Při proměně pěšce se zeptá na figuru.

- `◀` v režimu analýzy vezme poslední vlastní tah zpět,
- **↩ Zpět na partii** (objeví se vlevo dole) opustí analýzu a vrátí se do partie,
- klik do seznamu tahů nebo skok na jiný tah analýzu také ukončí.

## Soubory

| Soubor | Obsah |
|--------|-------|
| `main.py` | spouštěč |
| `main_window.py` | hlavní okno, navigace, propojení částí |
| `board_widget.py` | vykreslení šachovnice (chess.svg → QSvgWidget), velká i malá |
| `pgn_game.py` | načtení PGN, model partie a pozic |
| `engine_analyzer.py` | vlákno s UCI enginem a průběžnou analýzou pozice |
| `game_analyzer.py` | vlákno pro rozbor celé partie (značky chyb, ACPL, nejlepší tahy) |
| `move_class.py` | klasifikace tahů ve stylu chess.com (Nejlepší…Hrubka + Brilantní/Skvělý tah/Přehlédnutí) |
| `accuracy.py` | metriky přesnosti z hodnocení partie (win%, přesnost, EP, T1) |
| `accuracy_batch.py` | vlákno pro dávkový rozbor přesnosti přes databázi (umí N enginů paralelně) |
| `analysis_cache.py` | thread-safe cache rozborů partií (`analysis_cache.json`) |
| `engine_perf.py` | výchozí nastavení enginu (vlákna, hash, `UCI_ShowWDL`), `apply_engine_options` + naměřená čísla; `analyse_boards_parallel` (paralelní rozbor 1 partie) je v `game_analyzer.py` |
| `charakter.py` | charakter partie/pozice – fázový index, volatilita, dotahování, kritičnost, ostrost |
| `stats_util.py` | obecné statistické nástroje – Wilson, shrinkage, Elo, Kaplan–Meier, štěstí, logistická regrese |
| `anomaly.py` | detekce odlehlých partií (Mahalanobis + van der Waerden + reweighting), bez enginu |
| `eval_bar.py` | svislý ukazatel hodnocení pozice |
| `filters.py` | filtr databáze (rok, tempo, síla soupeře) |
| `player_analysis.py` | výpočet heatmapy a stromu zahájení z pozice |
| `heatmap_widget.py` | vykreslení heatmapy (QPainter) |
| `histogram_widget.py` | histogram se zvonovou (normální) křivkou a stat-boxem – styl Minitab (QPainter) |
| `time_heatmap_widget.py` | heatmapa winrate podle dne v týdnu a hodiny (QPainter) |
| `patterns.py` | statistické vzorce partií (rošáda, dámy, materiál, struktura, …) + lehké průchody pro kartu Grafy |
| `openings.py` | zařazení partií podle zahájení (ECO), diverzita repertoáru, hloubka teorie/book exit |
| `eco.tsv` | databáze zahájení ECO (lichess `chess-openings`, ~3800 variant) |
| `endgames.py` | rozpoznání a kategorizace koncovek |
| `tactics.py` | taktické úlohy z rozboru partií: motivy, obtížnost, tolerance, opakování SM-2 (`tactics_progress.json`) |
| `motifs.py` | rozpoznání taktických motivů jednoho tahu (mat, vidlička, vazba, oběť…) – bez enginu |
| `guess_move.py` | trénink „Hádej tah" – hádání tahů v rozebrané partii proti enginu (dialog) |
| `player_report.py` | uložené snímky statistik hráčů + metriky pro porovnání (`player_reports.json`) |
| `report_charts.py` | srovnávací grafy pro kartu Report (jedna série na hráče, ze snímků) |
| `report_export.py` | export porovnání hráčů do PDF (`QTextDocument` → `QPrinter`) |
| `sample.pgn` | ukázkové partie (Immortal Game, Opera Game) |

## Omezení

- Přehrává se **hlavní linie** partie; varianty (vedlejší tahy) v PGN se ignorují.
- NAG značky (`$1`, `!?`…) z PGN se nezobrazují, textové komentáře ano.
  Značky `?!` / `?` / `??` u tahů pocházejí z **rozboru partie enginem**, ne z PGN.
