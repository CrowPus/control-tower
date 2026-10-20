import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { errorMessage } from "./api";

/** A change with its outcome: the server's message on success, its reason on failure. */
export function useAction<A, R>(fn: (arg: A) => Promise<R>, options: { invalidate?: unknown[][]; done?: (result: R) => string | void } = {}) {
  const client = useQueryClient();
  const [notice, setNotice] = useState<{ tone: "success" | "danger"; text: string } | null>(null);
  const mutation = useMutation({
    mutationFn: fn,
    onMutate: () => setNotice(null),
    onSuccess: async (result) => {
      for (const key of options.invalidate ?? []) await client.invalidateQueries({ queryKey: key });
      const text = options.done?.(result) ?? (result as { message?: string } | null)?.message;
      if (text) setNotice({ tone: "success", text });
    },
    onError: (error) => setNotice({ tone: "danger", text: errorMessage(error) }),
  });
  return { run: mutation.mutate, busy: mutation.isPending, variables: mutation.variables, notice, clear: () => setNotice(null) };
}
