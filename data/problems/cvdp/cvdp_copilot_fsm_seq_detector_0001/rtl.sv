module fsm_seq_detector (
    input logic clk_in,
    input logic rst_in,
    input logic seq_in,
    output logic seq_detected
);
    typedef enum logic [2:0] {S0, S1, S2, S3, S4, S5, S6, S7} state_t;
    state_t state, next_state;
    always_comb begin
        case (state)
            S0: next_state = seq_in ? S1 : S0;
            S1: next_state = seq_in ? S1 : S2;
            S2: next_state = seq_in ? S3 : S0;
            S3: next_state = seq_in ? S4 : S2;
            S4: next_state = seq_in ? S1 : S5;
            S5: next_state = seq_in ? S3 : S6;
            S6: next_state = seq_in ? S1 : S7;
            S7: next_state = seq_in ? S1 : S0;
            default: next_state = S0;
        endcase
    end
    always_ff @(posedge clk_in or posedge rst_in) begin
        if (rst_in) begin
            state <= S0;
            seq_detected <= 0;
        end else begin
            state <= next_state;
            seq_detected <= (state == S7) && seq_in;
        end
    end
endmodule
