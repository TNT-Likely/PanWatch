package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/injoyai/tdx"
)

// KlineJSON represents a single K-line data point in JSON output.
type KlineJSON struct {
	Date   string  `json:"date"`
	Open   float64 `json:"open"`
	Close  float64 `json:"close"`
	High   float64 `json:"high"`
	Low    float64 `json:"low"`
	Volume float64 `json:"volume"`
}

func main() {
	symbol := flag.String("symbol", "sh600519", "Stock code with prefix (e.g. sh600519, sz000001)")
	days := flag.Int("days", 60, "Number of trading days to fetch")
	host := flag.String("host", "124.71.187.122:7709", "TDX server address (host:port)")
	timeout := flag.Int("timeout", 5, "Connection timeout in seconds")
	ktype := flag.String("type", "day", "K-line type: day / 5m / 15m / 30m / 60m")
	flag.Parse()

	if *days < 1 {
		*days = 60
	}
	if *days > 800 {
		*days = 800
	}

	// Save real stdout fd; redirect os.Stdout to devnull to suppress library logs
	realStdout := os.Stdout
	devnull, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open devnull failed: %v\n", err)
		os.Exit(1)
	}
	os.Stdout = devnull

	// Connect to TDX server (library logs go to devnull)
	cli, err := tdx.Dial(*host)
	if err != nil {
		os.Stdout = realStdout
		devnull.Close()
		fmt.Fprintf(os.Stderr, "connection failed: %v\n", err)
		os.Exit(1)
	}
	defer cli.Close()

	// Set timeout
	cli.SetTimeout(time.Duration(*timeout) * time.Second)

	// Fetch K-line data based on type
	var klines []KlineJSON

	switch *ktype {
	case "5m":
		resp, err := cli.GetKline5Minute(*symbol, 0, uint16(*days))
		if err != nil {
			os.Stdout = realStdout
			devnull.Close()
			fmt.Fprintf(os.Stderr, "get 5m kline failed: %v\n", err)
			os.Exit(1)
		}
		if resp != nil {
			klines = make([]KlineJSON, 0, len(resp.List))
			for _, k := range resp.List {
				klines = append(klines, KlineJSON{
					Date:   k.Time.Format("2006-01-02 15:04"),
					Open:   k.Open.Float64(),
					Close:  k.Close.Float64(),
					High:   k.High.Float64(),
					Low:    k.Low.Float64(),
					Volume: float64(k.Volume),
				})
			}
		}
	case "15m":
		resp, err := cli.GetKline15Minute(*symbol, 0, uint16(*days))
		if err != nil {
			os.Stdout = realStdout
			devnull.Close()
			fmt.Fprintf(os.Stderr, "get 15m kline failed: %v\n", err)
			os.Exit(1)
		}
		if resp != nil {
			klines = make([]KlineJSON, 0, len(resp.List))
			for _, k := range resp.List {
				klines = append(klines, KlineJSON{
					Date:   k.Time.Format("2006-01-02 15:04"),
					Open:   k.Open.Float64(),
					Close:  k.Close.Float64(),
					High:   k.High.Float64(),
					Low:    k.Low.Float64(),
					Volume: float64(k.Volume),
				})
			}
		}
	case "30m":
		resp, err := cli.GetKline30Minute(*symbol, 0, uint16(*days))
		if err != nil {
			os.Stdout = realStdout
			devnull.Close()
			fmt.Fprintf(os.Stderr, "get 30m kline failed: %v\n", err)
			os.Exit(1)
		}
		if resp != nil {
			klines = make([]KlineJSON, 0, len(resp.List))
			for _, k := range resp.List {
				klines = append(klines, KlineJSON{
					Date:   k.Time.Format("2006-01-02 15:04"),
					Open:   k.Open.Float64(),
					Close:  k.Close.Float64(),
					High:   k.High.Float64(),
					Low:    k.Low.Float64(),
					Volume: float64(k.Volume),
				})
			}
		}
	case "60m":
		resp, err := cli.GetKline60Minute(*symbol, 0, uint16(*days))
		if err != nil {
			os.Stdout = realStdout
			devnull.Close()
			fmt.Fprintf(os.Stderr, "get 60m kline failed: %v\n", err)
			os.Exit(1)
		}
		if resp != nil {
			klines = make([]KlineJSON, 0, len(resp.List))
			for _, k := range resp.List {
				klines = append(klines, KlineJSON{
					Date:   k.Time.Format("2006-01-02 15:04"),
					Open:   k.Open.Float64(),
					Close:  k.Close.Float64(),
					High:   k.High.Float64(),
					Low:    k.Low.Float64(),
					Volume: float64(k.Volume),
				})
			}
		}
	default: // "day" and fallback
		resp, err := cli.GetKlineDay(*symbol, 0, uint16(*days))
		if err != nil {
			os.Stdout = realStdout
			devnull.Close()
			fmt.Fprintf(os.Stderr, "get kline failed: %v\n", err)
			os.Exit(1)
		}
		if resp != nil {
			klines = make([]KlineJSON, 0, len(resp.List))
			for _, k := range resp.List {
				klines = append(klines, KlineJSON{
					Date:   k.Time.Format("2006-01-02"),
					Open:   k.Open.Float64(),
					Close:  k.Close.Float64(),
					High:   k.High.Float64(),
					Low:    k.Low.Float64(),
					Volume: float64(k.Volume),
				})
			}
		}
	}

	// Restore real stdout before printing JSON
	os.Stdout = realStdout
	devnull.Close()

	if len(klines) == 0 {
		fmt.Println("[]")
		return
	}

	// Output JSON to real stdout
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(klines); err != nil {
		fmt.Fprintf(os.Stderr, "json encode failed: %v\n", err)
		os.Exit(1)
	}
}