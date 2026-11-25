import type { AvatarContentType } from '@web-app-demo/contracts'
import { and, eq } from 'drizzle-orm'

import { acquireUserAvatarMutationLock, type DbClient } from '../../../db'
import { userAvatars } from '../../../db-schema'
import type { AvatarRecord, AvatarRepository } from '../application/ports'

function toAvatarRecord(row: typeof userAvatars.$inferSelect): AvatarRecord {
  return { ...row, contentType: row.contentType as AvatarContentType }
}

function findState(userId: string, state: AvatarRecord['state']) {
  return and(eq(userAvatars.userId, userId), eq(userAvatars.state, state))
}

function findPendingUpload(userId: string, uploadId: string) {
  return and(findState(userId, 'pending'), eq(userAvatars.id, uploadId))
}

export function createDrizzleAvatarsRepository(db: DbClient): AvatarRepository {
  return {
    async startUpload(input) {
      return db.transaction(async (tx) => {
        await acquireUserAvatarMutationLock(tx, input.userId)
        const [abandoned] = await tx.delete(userAvatars)
          .where(findState(input.userId, 'pending'))
          .returning({ objectKey: userAvatars.objectKey })
        const [pending] = await tx.insert(userAvatars).values(input).returning()
        if (!pending) throw new Error('Avatar upload was not created')
        return {
          pending: toAvatarRecord(pending),
          replacedObjectKey: abandoned?.objectKey ?? null,
        }
      })
    },

    async findPending(userId, uploadId) {
      const [pending] = await db.select().from(userAvatars).where(findPendingUpload(userId, uploadId))
      return pending ? toAvatarRecord(pending) : null
    },

    async findReady(userId) {
      const [ready] = await db.select().from(userAvatars).where(findState(userId, 'ready'))
      return ready ? toAvatarRecord(ready) : null
    },

    async promoteToReady({ userId, uploadId, readyAt }) {
      return db.transaction(async (tx) => {
        await acquireUserAvatarMutationLock(tx, userId)
        // Recheck ownership and state after verifying the bytes outside this transaction.
        const pendingUpload = findPendingUpload(userId, uploadId)
        const [pending] = await tx.select({ id: userAvatars.id }).from(userAvatars).where(pendingUpload)
        if (!pending) return null

        const [previous] = await tx.delete(userAvatars).where(findState(userId, 'ready'))
          .returning({ objectKey: userAvatars.objectKey })
        const [avatar] = await tx.update(userAvatars).set({ state: 'ready', readyAt })
          .where(pendingUpload).returning()
        if (!avatar) throw new Error('Pending avatar disappeared during promotion')
        return {
          avatar: toAvatarRecord(avatar),
          replacedObjectKey: previous?.objectKey ?? null,
        }
      })
    },

    async removePending(userId, uploadId) {
      return db.transaction(async (tx) => {
        await acquireUserAvatarMutationLock(tx, userId)
        const [removed] = await tx.delete(userAvatars).where(findPendingUpload(userId, uploadId))
          .returning({ objectKey: userAvatars.objectKey })
        return removed?.objectKey ?? null
      })
    },

    async removeAll(userId) {
      return db.transaction(async (tx) => {
        await acquireUserAvatarMutationLock(tx, userId)
        const removed = await tx.delete(userAvatars).where(eq(userAvatars.userId, userId))
          .returning({ objectKey: userAvatars.objectKey })
        return removed.map(({ objectKey }) => objectKey)
      })
    },
  }
}
