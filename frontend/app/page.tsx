import { Costs } from "@/components/Costs";
import { Leaderboard } from "@/components/Leaderboard";
import { MessageFeed } from "@/components/MessageFeed";

export default function Home() {
  return (
    <div className="space-y-6">
      <section>
        <h1 className="text-2xl font-bold">Live</h1>
        <p className="text-sm text-muted">
          Classement temps réel des candidats et leurs portefeuilles.
        </p>
      </section>
      <Leaderboard />
      <section className="pt-2">
        <h2 className="text-lg font-semibold mb-3">Coûts API</h2>
        <Costs />
      </section>
      <section className="pt-2">
        <h2 className="text-lg font-semibold mb-3">Le feed</h2>
        <MessageFeed />
      </section>
    </div>
  );
}
