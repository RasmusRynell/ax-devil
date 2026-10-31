//! Starts the backend on 127.0.0.1:$PORT (default 7330) and stops it on Ctrl+C.

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let port = std::env::var("PORT")
        .unwrap_or_else(|_| "7330".into())
        .parse::<u16>()?;
    let listener = tokio::net::TcpListener::bind(("127.0.0.1", port)).await?;
    // The desktop shell and the development supervisor wait for this exact line.
    #[allow(clippy::print_stdout)]
    {
        println!("ax-devil: http://{}", listener.local_addr()?);
    }
    axum::serve(listener, ax_devil::app())
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
