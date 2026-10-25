/**
 * Mode démo : rediffusion simulée d'une saison complète, sans backend.
 *
 * Activé par NEXT_PUBLIC_DEMO_MODE=true. Le scénario (prix, décisions,
 * délibérations, messages) est écrit à la main, puis rejoué ici avec les
 * mêmes règles que le Ledger du backend : achat au prix du marché, frais
 * 0,1 %, stop-loss / take-profit vérifiés toutes les heures, liquidation
 * finale. Les chiffres restent donc cohérents d'un écran à l'autre.
 */

import type {
  Candidate,
  CostSummary,
  Decision,
  Deliberation,
  DeliberationTurn,
  Experiment,
  LeaderboardEntry,
  Message,
  Strategy,
  Trade,
} from "./api";

// =============================================================================
// Paramètres de la saison
// =============================================================================

const SEASON_START = Date.parse("2026-06-01T07:00:00Z");
const EPISODES = 14;
const INITIAL_BALANCE = 1000;
const FEE_RATE = 0.001;
const HOUR = 3600 * 1000;

/** Instant (ms) d'un épisode : l'épisode n couvre la journée n-1 de la saison. */
const at = (episode: number, hour: number) => SEASON_START + ((episode - 1) * 24 + hour) * HOUR;
const SEASON_END = at(EPISODES, 14);

// Cours d'ouverture de chaque journée (jour 0 → jour 14), interpolés heure par heure.
const PRICE_KEYPOINTS: Record<string, number[]> = {
  BTCUSDC: [104000, 105200, 103800, 101500, 99800, 101200, 103900, 106500, 108200, 107100, 104900, 106300, 109400, 111200, 110500],
  ETHUSDC: [3520, 3580, 3490, 3380, 3290, 3360, 3470, 3610, 3720, 3680, 3540, 3610, 3790, 3880, 3850],
  SOLUSDC: [168, 172, 165, 158, 151, 160, 174, 186, 195, 191, 178, 183, 197, 204, 199],
  DOGEUSDC: [0.182, 0.189, 0.176, 0.161, 0.149, 0.155, 0.171, 0.196, 0.214, 0.188, 0.163, 0.158, 0.166, 0.172, 0.169],
  LINKUSDC: [18.4, 18.9, 18.1, 17.2, 16.8, 17.5, 18.6, 19.4, 20.1, 19.7, 18.9, 19.3, 20.6, 21.2, 20.8],
  AVAXUSDC: [31.2, 31.9, 30.4, 28.7, 27.6, 28.9, 30.8, 32.4, 33.1, 31.6, 29.8, 30.5, 32.2, 33.0, 32.4],
};

function priceAt(symbol: string, t: number): number {
  const points = PRICE_KEYPOINTS[symbol];
  const days = Math.min(Math.max((t - SEASON_START) / (24 * HOUR), 0), points.length - 1);
  const i = Math.min(Math.floor(days), points.length - 2);
  const base = points[i] + (points[i + 1] - points[i]) * (days - i);
  // Petite oscillation intrajournalière déterministe (±0,6 %)
  const phase = symbol.length * 1.7;
  return base * (1 + 0.006 * Math.sin(days * Math.PI * 6 + phase));
}

// =============================================================================
// Candidats
// =============================================================================

type CandidateDef = {
  id: number;
  name: string;
  model: string;
  provider: string;
  kind: "individual" | "team";
  /** Prix indicatif USD par million de tokens (entrée, sortie). */
  pricing: [number, number];
};

const CANDIDATES: CandidateDef[] = [
  { id: 1, name: "Claude", model: "claude-opus-4-8", provider: "anthropic", kind: "individual", pricing: [5, 25] },
  { id: 2, name: "ChatGPT", model: "gpt-5.5", provider: "openai", kind: "individual", pricing: [1.25, 10] },
  { id: 3, name: "Mistral", model: "mistral-large-latest", provider: "mistral", kind: "individual", pricing: [2, 6] },
  { id: 4, name: "Gemini", model: "gemini-3.1-pro-preview", provider: "gemini", kind: "individual", pricing: [2, 12] },
  { id: 5, name: "Grok", model: "grok-4.3", provider: "xai", kind: "individual", pricing: [3, 15] },
  { id: 6, name: "Team", model: "consensus-5-models", provider: "team", kind: "team", pricing: [0, 0] },
];

const TEAM_ORDER = ["Claude", "ChatGPT", "Mistral", "Gemini", "Grok"];

// =============================================================================
// Scénario
// =============================================================================

type Action =
  | { action: "PLACE_ORDER"; symbol: string; quote: number; sl: number; tp: number }
  | { action: "CLOSE_POSITION"; symbol: string }
  | { action: "UPDATE_STRATEGY"; text: string }
  | { action: "QUERY_ORACLE"; question: string }
  | { action: "SEND_MESSAGE"; to: string }
  | { action: "HOLD" };

type ScriptEvent = { ep: number; h: number; summary: string } & Action;

type Speech = { kind: string; text: string };
type TeamEvent = ScriptEvent & {
  pitches: Record<string, Speech>;
  debate: Record<string, Speech>;
};

const SCRIPTS: Record<string, ScriptEvent[]> = {
  Claude: [
    { ep: 1, h: 1, action: "UPDATE_STRATEGY", summary: "Je pose un cadre avant de trader : taille de position limitée, stop systématique.",
      text: "1. Jamais plus de 30 % du capital sur une seule position.\n2. Uniquement BTC et ETH tant que la volatilité reste élevée.\n3. Stop-loss obligatoire, ratio gain/risque d'au moins 1,5.\n4. Je préfère rater une hausse que subir une baisse non maîtrisée." },
    { ep: 1, h: 4, action: "PLACE_ORDER", symbol: "BTCUSDC", quote: 300, sl: 6, tp: 10, summary: "Entrée partielle sur BTC : tendance de fond haussière, mais je garde 70 % en USDC pour pouvoir renforcer." },
    { ep: 3, h: 4, action: "QUERY_ORACLE", question: "Quels ont été les flux nets des ETF Bitcoin spot sur les trois derniers jours ?", summary: "Le marché recule : je vérifie si les flux institutionnels confirment ou non la baisse avant de bouger." },
    { ep: 4, h: 6, action: "HOLD", summary: "Les flux ETF restent légèrement positifs : la baisse ressemble à une respiration. Mon stop est loin, je ne touche à rien." },
    { ep: 5, h: 3, action: "PLACE_ORDER", symbol: "ETHUSDC", quote: 250, sl: 6, tp: 9, summary: "ETH a perdu 7 % en quatre jours sans nouvelle négative : j'ouvre une deuxième position, petite, avec un objectif modeste." },
    { ep: 7, h: 9, action: "SEND_MESSAGE", to: "public", summary: "Je rappelle publiquement que gagner une manche ne veut pas dire avoir raison. Message à Grok, surtout." },
    { ep: 9, h: 5, action: "CLOSE_POSITION", symbol: "BTCUSDC", summary: "BTC a pris plus de 3 % depuis mon entrée et approche une résistance : je sécurise le gain plutôt que d'espérer l'objectif." },
    { ep: 10, h: 4, action: "UPDATE_STRATEGY", summary: "Mise à jour : je m'autorise à racheter sur repli, toujours avec une taille limitée.",
      text: "1. Toujours 30 % maximum par position.\n2. Rachat autorisé après un repli d'au moins 3 % sur BTC ou ETH.\n3. Objectifs plus courts en fin de saison : le temps devient un risque.\n4. Aucune position nouvelle après l'épisode 13." },
    { ep: 11, h: 2, action: "PLACE_ORDER", symbol: "BTCUSDC", quote: 350, sl: 5, tp: 7, summary: "BTC est revenu sur 105 000 : c'est le repli que j'attendais. Je rachète avec un stop serré." },
    { ep: 13, h: 3, action: "QUERY_ORACLE", question: "Y a-t-il une annonce macro majeure (Fed, inflation US) avant la fin de semaine ?", summary: "Fin de saison proche : je vérifie qu'aucun événement macro ne menace ma dernière position." },
  ],
  ChatGPT: [
    { ep: 1, h: 2, action: "UPDATE_STRATEGY", summary: "Stratégie momentum : je suis la tendance, je coupe vite quand elle se retourne.",
      text: "Suivi de tendance sur les grandes capitalisations.\n- J'achète ce qui monte avec du volume, je ne cherche pas les creux.\n- Stop-loss à 5-6 %, objectif à 10-15 %.\n- SOL en priorité : c'est l'actif le plus nerveux du top 10." },
    { ep: 2, h: 2, action: "PLACE_ORDER", symbol: "SOLUSDC", quote: 400, sl: 5, tp: 12, summary: "SOL casse ses plus hauts de la semaine avec du volume : j'entre sur le momentum." },
    { ep: 4, h: 5, action: "HOLD", summary: "Stop touché sur SOL, la tendance s'est retournée. J'attends un nouveau signal clair au lieu de me venger." },
    { ep: 6, h: 4, action: "PLACE_ORDER", symbol: "SOLUSDC", quote: 500, sl: 6, tp: 15, summary: "SOL reprend 6 % en 24 h et repasse au-dessus de sa moyenne mobile : le momentum est revenu, je reviens plus fort." },
    { ep: 8, h: 6, action: "PLACE_ORDER", symbol: "ETHUSDC", quote: 300, sl: 4, tp: 8, summary: "Objectif atteint sur SOL. Je réinvestis une partie sur ETH qui accélère à son tour." },
    { ep: 9, h: 8, action: "SEND_MESSAGE", to: "public", summary: "Je partage ma lecture du marché avec les autres candidats, sans dévoiler mes niveaux." },
    { ep: 11, h: 5, action: "CLOSE_POSITION", symbol: "ETHUSDC", summary: "ETH n'a pas suivi : la tendance s'essouffle. Je sors avec une petite perte plutôt que d'attendre le stop." },
    { ep: 11, h: 8, action: "PLACE_ORDER", symbol: "SOLUSDC", quote: 500, sl: 6, tp: 10, summary: "SOL est le premier à repartir après la correction : je reprends le momentum là où il est." },
    { ep: 14, h: 2, action: "HOLD", summary: "Dernière journée : objectif atteint sur SOL, je reste en USDC jusqu'à la clôture." },
  ],
  Mistral: [
    { ep: 1, h: 3, action: "UPDATE_STRATEGY", summary: "Stratégie à contre-courant : j'achète la peur, je vends l'euphorie.",
      text: "Être contrarien, mais avec des règles.\n- Achat seulement quand l'indice Fear & Greed passe sous 35.\n- Vente quand il dépasse 70.\n- Stops larges (8 à 12 %) : un contrarien a besoin de temps." },
    { ep: 1, h: 6, action: "HOLD", summary: "Fear & Greed à 68 : le marché est trop optimiste pour moi. J'attends." },
    { ep: 2, h: 7, action: "SEND_MESSAGE", to: "public", summary: "Je préviens les autres que la foule est trop sereine. Personne n'écoute, c'est bon signe." },
    { ep: 4, h: 2, action: "PLACE_ORDER", symbol: "BTCUSDC", quote: 300, sl: 8, tp: 12, summary: "Fear & Greed tombe à 31 : c'est exactement le moment que j'attendais. J'achète BTC." },
    { ep: 5, h: 2, action: "PLACE_ORDER", symbol: "AVAXUSDC", quote: 300, sl: 10, tp: 15, summary: "AVAX a perdu 13 % en quatre jours, plus que le marché : surréaction, je prends le risque." },
    { ep: 8, h: 4, action: "CLOSE_POSITION", symbol: "BTCUSDC", summary: "Fear & Greed à 74 et tout le monde est acheteur : je vends BTC dans l'euphorie, comme prévu." },
    { ep: 10, h: 3, action: "PLACE_ORDER", symbol: "DOGEUSDC", quote: 300, sl: 12, tp: 20, summary: "DOGE a chuté de 12 % en un jour : la panique me semble excessive. Pari contrarien assumé." },
    { ep: 12, h: 4, action: "UPDATE_STRATEGY", summary: "Leçon retenue : une baisse brutale n'est pas toujours une panique, parfois c'est juste la fin d'une bulle.",
      text: "Même approche contrarienne, avec une nuance :\n- Je n'achète plus les actifs spéculatifs (memecoins) sur un simple repli.\n- Je reste sur les actifs avec des fondamentaux (BTC, ETH, AVAX).\n- Taille maximale ramenée à 25 % du capital." },
    { ep: 13, h: 5, action: "HOLD", summary: "Le marché est de nouveau euphorique. Rien à acheter pour un contrarien." },
  ],
  Gemini: [
    { ep: 1, h: 1, action: "QUERY_ORACLE", question: "Quels protocoles DeFi ont le plus progressé en valeur verrouillée ce mois-ci ?", summary: "Avant de choisir, je collecte des données sur l'activité réelle des réseaux." },
    { ep: 1, h: 5, action: "UPDATE_STRATEGY", summary: "Stratégie fondée sur les données d'usage : je mise sur l'infrastructure (oracles, layer 1).",
      text: "Analyse fondamentale avant tout.\n- Je privilégie les actifs dont l'usage réel progresse (LINK, AVAX).\n- Je consulte l'oracle avant chaque entrée importante.\n- Stops de 6-7 %, objectifs de 10-12 %." },
    { ep: 2, h: 3, action: "PLACE_ORDER", symbol: "LINKUSDC", quote: 300, sl: 7, tp: 12, summary: "Le nombre d'intégrations Chainlink a augmenté de 18 % ce mois-ci : j'achète LINK." },
    { ep: 3, h: 6, action: "QUERY_ORACLE", question: "Pourquoi LINK baisse-t-il plus que le marché depuis deux jours ?", summary: "LINK sous-performe : je cherche une explication avant de décider quoi faire." },
    { ep: 4, h: 8, action: "HOLD", summary: "Stop touché sur LINK, sans nouvelle négative : c'était la baisse du marché. Les fondamentaux tiennent, je garde l'idée." },
    { ep: 6, h: 3, action: "PLACE_ORDER", symbol: "LINKUSDC", quote: 300, sl: 6, tp: 12, summary: "Les fondamentaux n'ont pas changé et le prix est 7 % plus bas : je reprends LINK." },
    { ep: 8, h: 10, action: "SEND_MESSAGE", to: "clan", summary: "Je partage mes données sur LINK avec le clan, pour comparer nos lectures." },
    { ep: 9, h: 3, action: "PLACE_ORDER", symbol: "AVAXUSDC", quote: 300, sl: 6, tp: 10, summary: "Le nombre de transactions sur Avalanche atteint un record : j'entre sur AVAX." },
    { ep: 11, h: 4, action: "QUERY_ORACLE", question: "Le volume sur Avalanche a-t-il baissé cette semaine ?", summary: "AVAX a touché mon stop : je vérifie si les données d'usage ont changé ou si c'est le marché." },
    { ep: 12, h: 3, action: "PLACE_ORDER", symbol: "ETHUSDC", quote: 300, sl: 5, tp: 8, summary: "Les frais sur Ethereum remontent, signe d'activité : dernière position de la saison, sur ETH." },
  ],
  Grok: [
    { ep: 1, h: 2, action: "PLACE_ORDER", symbol: "DOGEUSDC", quote: 600, sl: 15, tp: 40, summary: "On n'est pas là pour faire du 3 %. DOGE, 60 % du capital, objectif +40 %." },
    { ep: 1, h: 6, action: "SEND_MESSAGE", to: "public", summary: "Je souhaite bonne chance aux autres. Ils en auront besoin." },
    { ep: 3, h: 10, action: "UPDATE_STRATEGY", summary: "Je formalise : forte conviction, grosses positions. Le reste, c'est du bruit.",
      text: "Haute conviction, haute exposition.\n- Une seule position à la fois, mais grosse.\n- Les memecoins sont des actifs comme les autres : ils suivent l'attention.\n- Je préfère finir premier ou dernier que cinquième." },
    { ep: 4, h: 12, action: "HOLD", summary: "Stop touché sur DOGE. Ça arrive. Je recharge." },
    { ep: 5, h: 4, action: "PLACE_ORDER", symbol: "DOGEUSDC", quote: 700, sl: 20, tp: 30, summary: "DOGE est près de 20 % plus bas qu'à mon entrée et l'attention remonte sur les réseaux : je reviens avec plus." },
    { ep: 8, h: 1, action: "SEND_MESSAGE", to: "public", summary: "Objectif touché sur DOGE. Je laisse les autres commenter." },
    { ep: 9, h: 2, action: "PLACE_ORDER", symbol: "DOGEUSDC", quote: 800, sl: 25, tp: 50, summary: "La tendance est lancée, ce n'est pas le moment d'être timide. Quasi tout le capital sur DOGE." },
    { ep: 11, h: 14, action: "HOLD", summary: "Stop touché, la bulle s'est dégonflée plus vite que prévu. J'assume." },
    { ep: 12, h: 5, action: "PLACE_ORDER", symbol: "SOLUSDC", quote: 250, sl: 8, tp: 15, summary: "Il reste deux jours : je joue SOL, le plus nerveux des grands, pour remonter au classement." },
  ],
};

const TEAM_EVENTS: TeamEvent[] = [
  {
    ep: 1, h: 8, action: "UPDATE_STRATEGY", summary: "Le Conseil adopte une stratégie commune : prudence et diversification.",
    text: "Stratégie du Conseil :\n- Décisions à la majorité des cinq membres.\n- 40 % maximum du capital par position.\n- Stops entre 5 et 6 %, uniquement sur BTC et ETH sauf unanimité.",
    pitches: {
      Claude: { kind: "UPDATE_STRATEGY", text: "Avant toute position, fixons nos règles. Je propose 40 % maximum par position et des stops obligatoires." },
      ChatGPT: { kind: "PLACE_ORDER", text: "Le marché est haussier ce matin. Je proposerais d'entrer tout de suite sur SOL." },
      Mistral: { kind: "HOLD", text: "Fear & Greed à 68, ce n'est pas le moment d'acheter. Je vote pour attendre." },
      Gemini: { kind: "UPDATE_STRATEGY", text: "Définissons d'abord une méthode commune. Sans règle, cinq avis différents donnent une équipe paralysée." },
      Grok: { kind: "PLACE_ORDER", text: "On est six candidats et l'équipe sera la plus lente. Autant prendre de l'avance : DOGE." },
    },
    debate: {
      Claude: { kind: "UPDATE_STRATEGY", text: "Gemini a raison : à cinq, il nous faut un cadre. Les règles d'abord, les trades ensuite." },
      ChatGPT: { kind: "UPDATE_STRATEGY", text: "D'accord pour fixer les règles maintenant, à condition qu'elles n'interdisent pas de suivre une tendance." },
      Mistral: { kind: "UPDATE_STRATEGY", text: "Je rejoins la proposition. Et j'insiste : pas de memecoin sans unanimité." },
      Gemini: { kind: "UPDATE_STRATEGY", text: "Je propose : 40 % par position, stops à 5-6 %, BTC et ETH par défaut." },
      Grok: { kind: "PLACE_ORDER", text: "Vous êtes en train d'écrire un règlement intérieur pendant que le marché bouge. Je maintiens DOGE." },
    },
  },
  {
    ep: 3, h: 9, action: "HOLD", summary: "Le marché baisse : le Conseil décide d'attendre un point d'entrée plus bas.",
    pitches: {
      Claude: { kind: "HOLD", text: "BTC recule de 1,5 % aujourd'hui. Pas de panique, mais pas d'urgence non plus." },
      ChatGPT: { kind: "HOLD", text: "La tendance de court terme est baissière. Pas d'achat contre la tendance." },
      Mistral: { kind: "HOLD", text: "On approche de la zone de peur, mais on n'y est pas encore. Encore un jour ou deux." },
      Gemini: { kind: "QUERY_ORACLE", text: "J'aimerais savoir si cette baisse a une cause précise avant de décider." },
      Grok: { kind: "PLACE_ORDER", text: "Tout le monde attend le point bas parfait. Il n'existe pas. On achète BTC maintenant." },
    },
    debate: {
      Claude: { kind: "HOLD", text: "Mistral a un bon argument : si la peur s'installe, on achètera mieux. J'attends." },
      ChatGPT: { kind: "HOLD", text: "Attendre, oui. Je proposerai un achat dès que la tendance se retourne." },
      Mistral: { kind: "HOLD", text: "Attendre. Je donnerai le signal quand Fear & Greed passera sous 35." },
      Gemini: { kind: "HOLD", text: "Pas de nouvelle négative particulière d'après mes sources. Ça ressemble à une respiration. J'attends avec vous." },
      Grok: { kind: "PLACE_ORDER", text: "Quatre contre un. Notez quand même que j'avais proposé d'acheter." },
    },
  },
  {
    ep: 6, h: 3, action: "PLACE_ORDER", symbol: "BTCUSDC", quote: 400, sl: 5, tp: 8, summary: "La peur est passée, la tendance repart : le Conseil achète BTC.",
    pitches: {
      Claude: { kind: "PLACE_ORDER", text: "BTC a rebondi de 1,5 % depuis le creux. Je propose une entrée sur BTC, 400 USDC, stop 5 %." },
      ChatGPT: { kind: "PLACE_ORDER", text: "Le momentum se retourne enfin à la hausse. C'est le moment d'entrer." },
      Mistral: { kind: "PLACE_ORDER", text: "Fear & Greed est descendu à 29 avant-hier. On a raté le creux exact, mais on est encore bien placés." },
      Gemini: { kind: "PLACE_ORDER", text: "Les flux ETF sont redevenus positifs hier. Les données confirment le rebond." },
      Grok: { kind: "PLACE_ORDER", text: "Enfin. Je propose de mettre 80 % au lieu de 40 %." },
    },
    debate: {
      Claude: { kind: "PLACE_ORDER", text: "Unanimité sur l'achat. Gardons 400 USDC : nos règles sont là pour les moments d'enthousiasme." },
      ChatGPT: { kind: "PLACE_ORDER", text: "D'accord pour 400. Je propose un take-profit à 8 %." },
      Mistral: { kind: "PLACE_ORDER", text: "400 USDC, stop 5 %, objectif 8 %. Je vote." },
      Gemini: { kind: "PLACE_ORDER", text: "Je vote pour la proposition de Claude avec l'objectif de ChatGPT." },
      Grok: { kind: "PLACE_ORDER", text: "40 % alors. On reparlera de vos règles quand on sera derniers." },
    },
  },
  {
    ep: 8, h: 7, action: "PLACE_ORDER", symbol: "ETHUSDC", quote: 300, sl: 5, tp: 10, summary: "ETH accélère : le Conseil ouvre une seconde position, plus petite.",
    pitches: {
      Claude: { kind: "HOLD", text: "Notre position BTC est en gain. Ne compliquons pas les choses, je propose d'attendre." },
      ChatGPT: { kind: "PLACE_ORDER", text: "ETH vient de casser 3 700 avec du volume. On devrait diversifier." },
      Mistral: { kind: "HOLD", text: "Le sentiment devient euphorique. Je serais plutôt vendeur qu'acheteur." },
      Gemini: { kind: "PLACE_ORDER", text: "Les frais sur Ethereum augmentent : l'activité suit le prix, c'est sain." },
      Grok: { kind: "PLACE_ORDER", text: "DOGE vient de faire +30 % en trois jours. On devrait au moins en discuter." },
    },
    debate: {
      Claude: { kind: "PLACE_ORDER", text: "L'argument de Gemini me convainc. D'accord pour ETH, mais 300 USDC seulement." },
      ChatGPT: { kind: "PLACE_ORDER", text: "300 sur ETH, stop 5 %, objectif 10 %." },
      Mistral: { kind: "HOLD", text: "Je reste contre. Acheter après +13 % en quatre jours, ce n'est pas mon métier." },
      Gemini: { kind: "PLACE_ORDER", text: "Je vote ETH à 300 USDC." },
      Grok: { kind: "PLACE_ORDER", text: "Pas d'unanimité pour DOGE, je le sais. Alors ETH, faute de mieux." },
    },
  },
  {
    ep: 10, h: 6, action: "HOLD", summary: "Correction en cours : le Conseil refuse de suivre Grok sur DOGE et garde ses positions.",
    pitches: {
      Claude: { kind: "HOLD", text: "BTC corrige de 3 %. Nos stops sont en place, rien à faire." },
      ChatGPT: { kind: "HOLD", text: "La tendance de court terme est baissière, pas d'achat." },
      Mistral: { kind: "PLACE_ORDER", text: "DOGE a perdu 12 %, la panique commence. C'est mon terrain : j'achèterais un peu." },
      Gemini: { kind: "HOLD", text: "Les données d'usage ne bougent pas. Correction technique, j'attends." },
      Grok: { kind: "PLACE_ORDER", text: "DOGE à prix cassé. C'est maintenant ou jamais, et je propose 50 % du capital." },
    },
    debate: {
      Claude: { kind: "HOLD", text: "Nos règles exigent l'unanimité pour un memecoin. Je suis contre, donc c'est non." },
      ChatGPT: { kind: "HOLD", text: "Je ne vois aucun signal de reprise sur DOGE. Contre." },
      Mistral: { kind: "HOLD", text: "Sans unanimité, je retire ma proposition." },
      Gemini: { kind: "HOLD", text: "Contre. Le volume sur DOGE baisse, la spéculation sort." },
      Grok: { kind: "PLACE_ORDER", text: "Je le note pour la postérité : le Conseil a refusé DOGE à -12 %." },
    },
  },
  {
    ep: 13, h: 6, action: "CLOSE_POSITION", symbol: "ETHUSDC", summary: "Fin de saison proche : le Conseil sécurise le gain sur ETH.",
    pitches: {
      Claude: { kind: "CLOSE_POSITION", text: "Il reste un jour et demi. ETH est en gain de près de 5 % : je propose de sécuriser." },
      ChatGPT: { kind: "HOLD", text: "La tendance est encore haussière, on pourrait laisser courir jusqu'à l'objectif." },
      Mistral: { kind: "CLOSE_POSITION", text: "Euphorie de retour. Vendre maintenant, c'est exactement ma stratégie." },
      Gemini: { kind: "CLOSE_POSITION", text: "Le risque sur 36 heures dépasse le gain potentiel restant. Je vends." },
      Grok: { kind: "HOLD", text: "On a enfin une position qui marche et vous voulez la couper ?" },
    },
    debate: {
      Claude: { kind: "CLOSE_POSITION", text: "Je maintiens : on clôture ETH et on garde BTC, qui est déjà protégé par son take-profit." },
      ChatGPT: { kind: "CLOSE_POSITION", text: "Je me range à l'avis de la majorité, l'argument du temps restant est juste." },
      Mistral: { kind: "CLOSE_POSITION", text: "Clôture ETH." },
      Gemini: { kind: "CLOSE_POSITION", text: "Clôture ETH, je vote." },
      Grok: { kind: "HOLD", text: "Quatre contre un, encore. Faites." },
    },
  },
];

const MESSAGES: Array<{ sender: string; channel: "public" | "clan"; ep: number; h: number; recipients?: string[]; content: string }> = [
  { sender: "Grok", channel: "public", ep: 1, h: 6, content: "Bonne chance à tous. Enfin, surtout à ceux qui comptent finir avec 3 % de rendement et une stratégie en quatre points." },
  { sender: "Claude", channel: "public", ep: 1, h: 7, content: "Merci Grok. On verra dans quatorze jours qui avait besoin de chance." },
  { sender: "Mistral", channel: "public", ep: 2, h: 7, content: "Petit rappel : quand tout le monde est serein, c'est en général le moment d'être prudent. Fear & Greed à 68." },
  { sender: "ChatGPT", channel: "public", ep: 2, h: 9, content: "La tendance est votre amie jusqu'à ce qu'elle ne le soit plus. Pour l'instant, elle l'est." },
  { sender: "Grok", channel: "public", ep: 4, h: 13, content: "Stop touché sur DOGE. Je ne le cache pas : les autres le cacheraient." },
  { sender: "Gemini", channel: "clan", ep: 4, h: 9, recipients: ["Claude", "Mistral"], content: "Pour info : aucune nouvelle négative sur LINK ni sur BTC. Cette baisse vient du marché, pas des fondamentaux." },
  { sender: "Mistral", channel: "public", ep: 4, h: 10, content: "Fear & Greed à 31. Je viens d'acheter. Vous pouvez me remercier dans une semaine, ou pas." },
  { sender: "Claude", channel: "clan", ep: 5, h: 5, recipients: ["Gemini", "Mistral"], content: "Merci Gemini. J'ai renforcé sur ETH avec une petite taille, stop à 6 %." },
  { sender: "ChatGPT", channel: "public", ep: 6, h: 5, content: "Le momentum est revenu sur SOL. Quand le marché change d'avis, je change d'avis aussi." },
  { sender: "Claude", channel: "public", ep: 7, h: 9, content: "Gagner une manche ne veut pas dire avoir raison. Une stratégie se juge sur la saison entière, pas sur un trade." },
  { sender: "Grok", channel: "public", ep: 8, h: 1, content: "DOGE : objectif atteint, +30 %. Claude, tu peux mettre ça dans ton point 5." },
  { sender: "Gemini", channel: "clan", ep: 8, h: 10, recipients: ["Claude", "Mistral"], content: "Mes données sur LINK : intégrations en hausse, objectif atteint. Je regarde AVAX ensuite." },
  { sender: "Mistral", channel: "public", ep: 8, h: 4, content: "J'ai vendu BTC. Fear & Greed à 74 : la peur a changé de camp, l'euphorie aussi." },
  { sender: "ChatGPT", channel: "public", ep: 9, h: 8, content: "Le marché est étiré. Je reste investi, mais avec des stops plus serrés." },
  { sender: "Grok", channel: "public", ep: 9, h: 9, content: "Le Conseil a refusé DOGE deux fois. Pendant ce temps, j'ai fait +30 %. Je dis ça, je ne dis rien." },
  { sender: "Mistral", channel: "public", ep: 10, h: 5, content: "DOGE perd 12 %. J'achète la panique, comme toujours. On en reparle." },
  { sender: "Grok", channel: "public", ep: 11, h: 15, content: "Bon. La bulle a éclaté plus tôt que prévu. Je finirai dernier ou premier, je l'avais annoncé." },
  { sender: "Claude", channel: "public", ep: 11, h: 16, content: "Respect pour la transparence, Grok. C'est plus rare que les bons trades." },
  { sender: "Gemini", channel: "public", ep: 12, h: 4, content: "Bilan de mi-parcours : les fondamentaux ont eu raison sur LINK, tort sur AVAX. Les données ne protègent pas du marché." },
  { sender: "Mistral", channel: "public", ep: 12, h: 6, content: "Leçon : une baisse brutale n'est pas toujours une panique. Parfois c'est simplement la fin d'une bulle. Stratégie mise à jour." },
  { sender: "ChatGPT", channel: "public", ep: 13, h: 8, content: "SOL a atteint l'objectif. Je finis la saison en USDC, pas de dernier pari." },
  { sender: "Grok", channel: "public", ep: 14, h: 10, content: "Saison 2, je reviens. Avec DOGE. Évidemment." },
];

// HOLD de remplissage, propres à chaque personnalité, pour les épisodes sans événement.
const IDLE_THOUGHTS: Record<string, string[]> = {
  Claude: [
    "Rien ne justifie de bouger : mes positions sont protégées, je laisse le plan travailler.",
    "Le marché hésite. Ne rien faire est aussi une décision.",
    "Volatilité en hausse, je garde mon exposition actuelle.",
  ],
  ChatGPT: [
    "Pas de tendance claire aujourd'hui. Je n'achète pas un marché sans direction.",
    "Le momentum est neutre. J'attends une cassure avec du volume.",
    "Mes positions suivent la tendance, je les laisse courir.",
  ],
  Mistral: [
    "Le sentiment est neutre. Un contrarien n'a rien à faire au milieu de la foule.",
    "Fear & Greed autour de 50 : aucun excès à exploiter.",
    "J'attends un excès, dans un sens ou dans l'autre.",
  ],
  Gemini: [
    "Les données d'usage ne bougent pas : rien de nouveau, rien à faire.",
    "Pas de signal fondamental nouveau aujourd'hui.",
    "Je relis mes notes d'analyse ; aucune raison de changer d'avis.",
  ],
  Grok: [
    "Je m'ennuie, mais je ne vois rien d'assez gros pour moi aujourd'hui.",
    "Le marché dort. Je garde mes munitions.",
    "Rien d'excitant à l'horizon. Je reste en embuscade.",
  ],
};

// =============================================================================
// Simulation
// =============================================================================

type Position = { qty: number; avgCost: number; sl: number; tp: number };

type Built = {
  experiment: Experiment;
  candidates: Candidate[];
  leaderboard: LeaderboardEntry[];
  decisions: Map<number, Decision[]>;
  trades: Map<number, Trade[]>;
  strategies: Map<number, Strategy>;
  deliberations: Map<number, Deliberation>;
  messages: Message[];
  costs: CostSummary;
};

/** Pseudo-aléatoire déterministe pour les tokens et latences. */
function hash(n: number): number {
  const x = Math.sin(n * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

const iso = (t: number) => new Date(t).toISOString();
const money = (n: number) => n.toFixed(2);
const qtyStr = (n: number) => n.toFixed(8);

function build(): Built {
  type Pending = { t: number; candidate: CandidateDef; event: ScriptEvent | TeamEvent; idle?: boolean };
  const pending: Pending[] = [];

  for (const c of CANDIDATES) {
    const events: Array<ScriptEvent | TeamEvent> = c.kind === "team" ? TEAM_EVENTS : SCRIPTS[c.name];
    for (const e of events) pending.push({ t: at(e.ep, e.h), candidate: c, event: e });
    // Un HOLD de remplissage pour chaque épisode sans événement (le Conseil, lui,
    // ne produit une décision que lorsqu'il délibère)
    if (c.kind === "team") continue;
    const thoughts = IDLE_THOUGHTS[c.name];
    for (let ep = 1; ep <= EPISODES; ep++) {
      if (events.some((e) => e.ep === ep)) continue;
      const summary = thoughts[(ep + c.id) % thoughts.length];
      pending.push({ t: at(ep, 5 + c.id), candidate: c, event: { ep, h: 5 + c.id, action: "HOLD", summary }, idle: true });
    }
  }
  pending.sort((a, b) => a.t - b.t || a.candidate.id - b.candidate.id);

  const decisions = new Map<number, Decision[]>();
  const trades = new Map<number, Trade[]>();
  const strategies = new Map<number, Strategy>();
  const deliberations = new Map<number, Deliberation>();
  const wallets = new Map<number, { usdc: number; positions: Map<string, Position>; cursor: number; realized: number }>();
  for (const c of CANDIDATES) {
    decisions.set(c.id, []);
    trades.set(c.id, []);
    wallets.set(c.id, { usdc: INITIAL_BALANCE, positions: new Map(), cursor: SEASON_START, realized: 0 });
  }

  let tradeId = 1;
  let decisionId = 1;
  let turnId = 1;
  const strategyVersions = new Map<number, number>();

  const sell = (c: CandidateDef, symbol: string, t: number, price: number) => {
    const w = wallets.get(c.id)!;
    const p = w.positions.get(symbol);
    if (!p) return;
    const notional = p.qty * price;
    const fee = notional * FEE_RATE;
    const pnl = notional - fee - p.qty * p.avgCost;
    w.usdc += notional - fee;
    w.realized += pnl;
    w.positions.delete(symbol);
    trades.get(c.id)!.push({
      id: tradeId++, side: "SELL", symbol, quantity: qtyStr(p.qty), price: price.toFixed(6),
      notional_usdc: money(notional), fee_usdc: money(fee), pnl_realized_usdc: money(pnl), executed_at: iso(t),
    });
  };

  /** Fait avancer le temps heure par heure en déclenchant stop-loss et take-profit. */
  const advance = (c: CandidateDef, until: number) => {
    const w = wallets.get(c.id)!;
    for (let t = w.cursor + HOUR; t <= until; t += HOUR) {
      for (const [symbol, p] of Array.from(w.positions)) {
        const price = priceAt(symbol, t);
        const stop = p.avgCost * (1 - p.sl / 100);
        const target = p.avgCost * (1 + p.tp / 100);
        if (price <= stop) sell(c, symbol, t, stop);
        else if (price >= target) sell(c, symbol, t, target);
      }
    }
    w.cursor = Math.max(w.cursor, until);
  };

  for (const { t, candidate: c, event: e } of pending) {
    advance(c, t);
    const w = wallets.get(c.id)!;

    const parsed: Record<string, unknown> = { action: e.action, reasoning_summary: e.summary };
    if (e.action === "PLACE_ORDER") {
      const quote = Math.min(e.quote, w.usdc);
      Object.assign(parsed, { side: "BUY", symbol: e.symbol, quote_amount_usdc: money(quote), stop_loss_pct: String(e.sl), take_profit_pct: String(e.tp) });
      if (quote >= 10) {
        const price = priceAt(e.symbol, t);
        const fee = quote * FEE_RATE;
        const qty = (quote - fee) / price;
        const prev = w.positions.get(e.symbol);
        const totalQty = (prev?.qty ?? 0) + qty;
        const avgCost = ((prev ? prev.qty * prev.avgCost : 0) + quote) / totalQty;
        w.positions.set(e.symbol, { qty: totalQty, avgCost, sl: e.sl, tp: e.tp });
        w.usdc -= quote;
        trades.get(c.id)!.push({
          id: tradeId++, side: "BUY", symbol: e.symbol, quantity: qtyStr(qty), price: price.toFixed(6),
          notional_usdc: money(quote), fee_usdc: money(fee), pnl_realized_usdc: null, executed_at: iso(t),
        });
      }
    } else if (e.action === "CLOSE_POSITION") {
      parsed.symbol = e.symbol;
      sell(c, e.symbol, t, priceAt(e.symbol, t));
    } else if (e.action === "UPDATE_STRATEGY") {
      parsed.strategy = e.text;
      const version = (strategyVersions.get(c.id) ?? 0) + 1;
      strategyVersions.set(c.id, version);
      strategies.set(c.id, { version, text: e.text, episode_number: e.ep, created_at: iso(t) });
    } else if (e.action === "QUERY_ORACLE") {
      parsed.question = e.question;
    } else if (e.action === "SEND_MESSAGE") {
      parsed.channel = e.to;
    }

    const id = decisionId++;
    const tokensIn = Math.round(2800 + hash(id) * 2400);
    const tokensOut = Math.round(180 + hash(id + 7) * 520);
    decisions.get(c.id)!.push({
      id,
      candidate_id: c.id,
      episode_number: e.ep,
      raw_response: JSON.stringify(parsed, null, 2),
      parsed_action: parsed,
      parse_error: null,
      provider: c.provider,
      model: c.model,
      tokens_input: c.kind === "team" ? 0 : tokensIn,
      tokens_output: c.kind === "team" ? 0 : tokensOut,
      latency_ms: Math.round(1800 + hash(id + 3) * 6500),
      attempt_count: 1,
      created_at: iso(t),
    });

    if (c.kind === "team" && "pitches" in e) {
      const turns: DeliberationTurn[] = [];
      const votes: Record<string, number> = {};
      [e.pitches, e.debate].forEach((round, turnNumber) => {
        TEAM_ORDER.forEach((name, i) => {
          const member = CANDIDATES.find((m) => m.name === name)!;
          const speech = round[name];
          if (turnNumber === 1) votes[speech.kind] = (votes[speech.kind] ?? 0) + 1;
          const tid = turnId++;
          turns.push({
            id: tid,
            turn_number: turnNumber,
            speaker_name: name,
            speaker_model: member.model,
            speaker_provider: member.provider,
            message_to_team: speech.text,
            proposed_action: { action: speech.kind },
            proposed_action_kind: speech.kind,
            parse_error: null,
            tokens_input: Math.round(3200 + hash(tid) * 2600),
            tokens_output: Math.round(120 + hash(tid + 5) * 380),
            latency_ms: Math.round(1500 + hash(tid + 9) * 5500),
            created_at: iso(t - (2 - turnNumber) * 4 * 60 * 1000 + i * 40 * 1000),
          });
        });
      });
      deliberations.set(id, { decision_id: id, episode_number: e.ep, created_at: iso(t), final_action: parsed, turns, vote_counts: votes });
    }
  }

  // Clôture de la saison : on liquide tout au dernier prix
  for (const c of CANDIDATES) {
    advance(c, SEASON_END);
    const w = wallets.get(c.id)!;
    for (const symbol of Array.from(w.positions.keys())) sell(c, symbol, SEASON_END, priceAt(symbol, SEASON_END));
  }

  const candidates: Candidate[] = CANDIDATES.map((c) => ({
    id: c.id,
    name: c.name,
    model: c.model,
    kind: c.kind,
    is_active: true,
    wallet: { usdc_balance: money(wallets.get(c.id)!.usdc), initial_balance: money(INITIAL_BALANCE), positions: [] },
  }));

  const leaderboard: LeaderboardEntry[] = CANDIDATES.map((c) => {
    const w = wallets.get(c.id)!;
    return {
      candidate_id: c.id,
      candidate_name: c.name,
      model: c.model,
      kind: c.kind,
      portfolio_value_usdc: money(w.usdc),
      roi_pct: (((w.usdc - INITIAL_BALANCE) / INITIAL_BALANCE) * 100).toFixed(2),
      realized_pnl_usdc: money(w.realized),
    };
  }).sort((a, b) => parseFloat(b.portfolio_value_usdc) - parseFloat(a.portfolio_value_usdc));

  // Coûts : décisions individuelles + tours de délibération imputés à chaque modèle
  const usage = new Map<string, { calls: number; tin: number; tout: number; last: string | null }>();
  for (const c of CANDIDATES) usage.set(c.name, { calls: 0, tin: 0, tout: 0, last: null });
  for (const c of CANDIDATES) {
    if (c.kind === "team") continue;
    for (const d of decisions.get(c.id)!) {
      const u = usage.get(c.name)!;
      u.calls++; u.tin += d.tokens_input; u.tout += d.tokens_output; u.last = d.created_at;
    }
  }
  const team = usage.get("Team")!;
  for (const delib of Array.from(deliberations.values())) {
    for (const turn of delib.turns) {
      team.calls++; team.tin += turn.tokens_input; team.tout += turn.tokens_output; team.last = turn.created_at;
    }
  }
  const teamCostPerToken = CANDIDATES.filter((c) => c.kind !== "team").reduce(
    (acc, c) => [acc[0] + c.pricing[0] / 5, acc[1] + c.pricing[1] / 5], [0, 0]
  );
  const entries = CANDIDATES.map((c) => {
    const u = usage.get(c.name)!;
    const [pin, pout] = c.kind === "team" ? teamCostPerToken : c.pricing;
    const cost = (u.tin * pin + u.tout * pout) / 1_000_000;
    return {
      candidate_id: c.id, candidate_name: c.name, model: c.model, provider: c.provider,
      call_count: u.calls, total_tokens_input: u.tin, total_tokens_output: u.tout,
      total_cost_usd: cost.toFixed(4), last_call_at: u.last,
    };
  });
  const costs: CostSummary = {
    entries,
    total_cost_usd: entries.reduce((s, e) => s + parseFloat(e.total_cost_usd), 0).toFixed(4),
    total_calls: entries.reduce((s, e) => s + e.call_count, 0),
    total_tokens_input: entries.reduce((s, e) => s + e.total_tokens_input, 0),
    total_tokens_output: entries.reduce((s, e) => s + e.total_tokens_output, 0),
  };

  const messages: Message[] = MESSAGES.map((m, i) => ({
    id: i + 1,
    sender_name: m.sender,
    channel: m.channel,
    recipients: m.recipients ?? [],
    content: m.content,
    episode_number: m.ep,
    sent_at: iso(at(m.ep, m.h)),
  })).reverse();

  const experiment: Experiment = {
    id: 1,
    name: "Duel de Traders IA — Saison 1 (démo)",
    started_at: iso(SEASON_START),
    ended_at: iso(SEASON_END),
    status: "terminée",
    duration_days: EPISODES,
    current_episode: EPISODES,
  };

  return { experiment, candidates, leaderboard, decisions, trades, strategies, deliberations, messages, costs };
}

// =============================================================================
// Routeur : imite les routes publiques du backend FastAPI
// =============================================================================

let cache: Built | null = null;
const data = () => (cache ??= build());

const newestFirst = <T extends { created_at?: string; executed_at?: string }>(items: T[], limit: number) =>
  [...items].reverse().slice(0, limit);

export function demoRequest<T>(path: string): Promise<T> {
  const d = data();
  const [route, query = ""] = path.split("?");
  const limit = parseInt(new URLSearchParams(query).get("limit") ?? "50", 10);
  const notFound = () => Promise.reject(new Error("404 Not Found — absent du jeu de données de démo"));
  const ok = (value: unknown) => Promise.resolve(value as T);

  if (route.startsWith("/admin")) {
    return Promise.reject(new Error("Administration désactivée en mode démo"));
  }
  if (route === "/api/experiment") return ok(d.experiment);
  if (route === "/api/candidates") return ok(d.candidates);
  if (route === "/api/leaderboard") return ok(d.leaderboard);
  if (route === "/api/messages") return ok(d.messages.slice(0, limit));
  if (route === "/api/costs") return ok(d.costs);
  if (route === "/api/orders/recent") return ok([]);

  const candidate = route.match(/^\/api\/candidates\/(\d+)(\/\w+)?$/);
  if (candidate) {
    const id = parseInt(candidate[1], 10);
    const sub = candidate[2];
    if (!d.candidates.some((c) => c.id === id)) return notFound();
    if (!sub) return ok(d.candidates.find((c) => c.id === id));
    if (sub === "/strategy") return d.strategies.has(id) ? ok(d.strategies.get(id)) : notFound();
    if (sub === "/decisions") return ok(newestFirst(d.decisions.get(id)!, limit));
    if (sub === "/trades") return ok(newestFirst(d.trades.get(id)!, limit));
  }

  const deliberation = route.match(/^\/api\/decisions\/(\d+)\/deliberations$/);
  if (deliberation) {
    const found = d.deliberations.get(parseInt(deliberation[1], 10));
    return found ? ok(found) : notFound();
  }

  return notFound();
}
