import { GlassMascot } from '@web-app-demo/mascot'

/** @deprecated Use GlassMascot directly. Kept for existing local imports. */
export function BlobAvatar({ seed, thinking = false, followPointer = true, className }: { seed: string; thinking?: boolean; followPointer?: boolean; className?: string }) {
  return <GlassMascot seed={seed} state={thinking ? 'thinking' : 'idle'} followPointer={followPointer} className={className} />
}
