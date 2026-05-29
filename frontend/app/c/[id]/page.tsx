import { CandidateDetail } from "@/components/CandidateDetail";

export default function CandidatePage({ params }: { params: { id: string } }) {
  return <CandidateDetail candidateId={params.id} />;
}
