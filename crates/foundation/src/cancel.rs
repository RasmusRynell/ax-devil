use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

/// A cancellation flag shared by every clone. Cancelling is permanent.
#[derive(Clone, Debug, Default)]
pub struct CancelToken {
    flag: Arc<AtomicBool>,
}

impl CancelToken {
    /// A token that is not cancelled.
    pub fn new() -> Self {
        Self::default()
    }

    /// Cancels this token and every clone of it.
    pub fn cancel(&self) {
        self.flag.store(true, Ordering::Release);
    }

    /// Whether any clone has been cancelled.
    pub fn is_cancelled(&self) -> bool {
        self.flag.load(Ordering::Acquire)
    }

    /// `Err(Cancelled)` once cancelled, so long work can stop with `?`.
    pub fn check(&self) -> Result<(), Cancelled> {
        if self.is_cancelled() {
            Err(Cancelled)
        } else {
            Ok(())
        }
    }
}

/// Work stopped because its token was cancelled.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Cancelled;

/// Why work stopped early. Cancellation is never an error value: generic error handling cannot turn it into a
/// failure. Failures convert with `.map_err(Interrupt::Failed)`.
#[derive(Debug)]
pub enum Interrupt<E> {
    /// The caller cancelled the work.
    Cancelled,
    /// The work failed.
    Failed(E),
}

impl<E> From<Cancelled> for Interrupt<E> {
    fn from(_: Cancelled) -> Self {
        Self::Cancelled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancelling_one_clone_cancels_all_and_stays_cancelled() {
        let token = CancelToken::new();
        let clone = token.clone();
        assert_eq!(token.check(), Ok(()));
        assert!(!clone.is_cancelled());

        clone.cancel();

        assert!(token.is_cancelled());
        assert_eq!(token.check(), Err(Cancelled));
        assert!(
            !CancelToken::new().is_cancelled(),
            "a new token is independent"
        );
    }

    #[test]
    fn question_mark_turns_cancellation_into_the_interrupt_not_a_failure() {
        fn work(token: &CancelToken) -> Result<u8, Interrupt<String>> {
            token.check()?;
            Err(Interrupt::Failed("broken".to_string()))
        }
        let token = CancelToken::new();
        assert!(matches!(work(&token), Err(Interrupt::Failed(reason)) if reason == "broken"));
        token.cancel();
        assert!(matches!(work(&token), Err(Interrupt::Cancelled)));
    }
}
