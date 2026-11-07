"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { JobView } from "@/components/JobView";
import { Skeleton } from "@/components/ui";

function Detail() {
  const { id } = useParams<{ id: string }>();
  return <JobView jobId={id} />;
}

export default function VideoDetailPage() {
  return (
    <div>
      <Link href="/videos" className="mb-4 inline-block text-sm text-zinc-500 hover:text-indigo-500">
        &larr; All videos
      </Link>
      <Suspense fallback={<Skeleton className="h-64 w-full" />}>
        <Detail />
      </Suspense>
    </div>
  );
}
