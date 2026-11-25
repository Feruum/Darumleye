import { agentSpecReviewSchema } from '@web-app-demo/contracts'

import exampleReport from './examples/fintech-payment-review.json'
import exampleSource from './examples/fintech-payment-spec.txt?raw'

export function loadExampleReport() {
  const result = agentSpecReviewSchema.parse(exampleReport)
  if (result.filename !== 'fintech-payment-spec.txt' || result.source !== 'txt') throw new Error('Example document mismatch')
  return result
}

export function createExampleFile() {
  return new File([exampleSource], 'fintech-payment-spec.txt', { type: 'text/plain' })
}
