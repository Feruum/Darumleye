import { queryOptions } from '@tanstack/react-query'

import { sessionQueryKeys } from '@/features/auth'
import type { AuthenticatedTransport } from '@/platform/api'
import { getAgentDocuments, getAgentMemories, getAgentModules, getCompletedReview, getCompletedReviews } from './api'

export const agentQueryKeys = {
  all: [...sessionQueryKeys.all, 'agent'] as const,
  modules: (ownerId?: string) => [...agentQueryKeys.all, 'modules', ownerId] as const,
  documents: (ownerId?: string) => [...agentQueryKeys.all, 'documents', ownerId] as const,
  completedReviews: (ownerId?: string) => [...agentQueryKeys.all, 'completed-reviews', ownerId] as const,
  completedReview: (ownerId: string | undefined, reviewId: string) => [...agentQueryKeys.all, 'completed-review', ownerId, reviewId] as const,
  memories: (ownerId?: string) => [...agentQueryKeys.all, 'memories', ownerId] as const,
}

export function agentModulesQueryOptions(transport: AuthenticatedTransport, ownerId?: string) {
  return queryOptions({
    queryKey: agentQueryKeys.modules(ownerId),
    queryFn: ({ signal }) => getAgentModules(transport, signal),
    enabled: !!ownerId,
  })
}

export function agentDocumentsQueryOptions(transport: AuthenticatedTransport, ownerId?: string) {
  return queryOptions({
    queryKey: agentQueryKeys.documents(ownerId),
    queryFn: ({ signal }) => getAgentDocuments(transport, signal),
    enabled: !!ownerId,
  })
}

export function completedReviewsQueryOptions(transport: AuthenticatedTransport, ownerId?: string) {
  return queryOptions({
    queryKey: agentQueryKeys.completedReviews(ownerId),
    queryFn: ({ signal }) => getCompletedReviews(transport, signal),
    enabled: !!ownerId,
  })
}

export function completedReviewQueryOptions(transport: AuthenticatedTransport, ownerId: string | undefined, reviewId: string) {
  return queryOptions({
    queryKey: agentQueryKeys.completedReview(ownerId, reviewId),
    queryFn: ({ signal }) => getCompletedReview(transport, reviewId, signal),
    enabled: !!ownerId,
    staleTime: Infinity,
    retry: false,
  })
}

export function agentMemoriesQueryOptions(transport: AuthenticatedTransport, ownerId?: string) {
  return queryOptions({
    queryKey: agentQueryKeys.memories(ownerId),
    queryFn: ({ signal }) => getAgentMemories(transport, signal),
    enabled: !!ownerId,
  })
}
