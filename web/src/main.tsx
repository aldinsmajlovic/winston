import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import { BrowserRouter } from 'react-router'
import { toast } from 'sonner'
import App from '@/App'
import { errorText } from '@/api/client'
import { ConfirmProvider } from '@/components/confirm-provider'
import { DraftProvider } from '@/components/draft-provider'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import './index.css'

// A failed action says why, unless its caller shows the error itself: an onError of its own, or
// meta.showsErrors for callers that catch (mutateAsync) or pass onError per call.
const mutationCache = new MutationCache({
  onError: (error, _variables, _result, mutation) => {
    if (mutation.options.onError || mutation.meta?.showsErrors) return
    toast.error(errorText(error))
  },
})
const queryClient = new QueryClient({ mutationCache, defaultOptions: { queries: { retry: 1 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <TooltipProvider>
            <ConfirmProvider>
              <DraftProvider>
                <App />
              </DraftProvider>
            </ConfirmProvider>
            <Toaster />
          </TooltipProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
