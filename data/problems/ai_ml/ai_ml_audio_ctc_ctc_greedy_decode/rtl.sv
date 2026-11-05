module ai_ml_audio_ctc_ctc_greedy_decode (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [511:0] in_scores,
    input logic [3:0] in_length,
    input logic [1:0] in_blank,
    output logic out_valid,
    input logic out_ready,
    output logic [3:0] out_count,
    output logic [15:0] out_tokens,
    output logic [23:0] out_times
);
    logic busy;
    logic [3:0] count;
    logic [15:0] tokens;
    logic [23:0] times;
    logic signed [15:0] best, score;
    logic [1:0] label, previous;

    assign in_ready = !busy || out_ready;
    assign out_valid = busy;
    always_comb begin
        count = 0;
        tokens = 0;
        times = 0;
        previous = 0;
        best = 0;
        score = 0;
        label = 0;
        for (int t = 0; t < 8; t++) begin
            if (t < int'(in_length)) begin
                best = $signed(in_scores[64*t +: 16]);
                label = 0;
                for (int k = 1; k < 4; k++) begin
                    score = $signed(in_scores[16*(4*t+k) +: 16]);
                    if (score > best) begin
                        best = score;
                        label = 2'(k);
                    end
                end
                if ((t == 0 || label != previous) && label != in_blank) begin
                    tokens[2*int'(count) +: 2] = label;
                    times[3*int'(count) +: 3] = 3'(t);
                    count = count + 1'b1;
                end
                previous = label;
            end
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_count <= 0;
            out_tokens <= 0;
            out_times <= 0;
        end else if (in_valid && in_ready) begin
            busy <= 1;
            out_count <= count;
            out_tokens <= tokens;
            out_times <= times;
        end else if (out_ready) begin
            busy <= 0;
        end
    end
endmodule
