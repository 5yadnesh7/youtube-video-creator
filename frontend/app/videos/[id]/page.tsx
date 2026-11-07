"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { JobView } from "@/components/JobView";
import { FOCUS } from "@/components/ResultView";
import { Skeleton } from "@/components/ui";

function Detail() {
  const { id } = useParams<{ id: string }>();
  return <JobView jobId={id} />;
}

export default function VideoDetailPage() {
  return (
    <div className="min-w-0">
      <Link
        href="/videos"
        className={`mb-2 inline-flex min-h-11 items-center rounded-lg pr-3 text-sm text-zinc-600 hover:text-indigo-700 dark:text-zinc-400 dark:hover:text-indigo-300 ${FOCUS}`}
      >
        &larr; All videos
      </Link>
      <Suspense fallback={<Skeleton className="h-64 w-full motion-reduce:animate-none" />}>
        <Detail />
      </Suspense>
    </div>
  );
}
