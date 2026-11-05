module ai_ml_audio_ctc_ctc_beam_search #(
    parameter int TOKEN_W = 8,
    parameter int TIME_W  = 8,
    parameter int MAX_T   = 16
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [TOKEN_W-1:0]           in_blank_token,
    input  logic [TIME_W-1:0]            in_length,
    input  logic [MAX_T*TOKEN_W-1:0]     in_tokens,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [TIME_W-1:0]            out_token_length,
    output logic [TIME_W-1:0]            out_timestep_length,
    output logic [MAX_T*TOKEN_W-1:0]     out_tokens,
    output logic [MAX_T*TIME_W-1:0]      out_timesteps
);

    localparam int COUNT_W = (MAX_T <= 1) ? 1 : $clog2(MAX_T + 1);

    logic busy;
    logic [TIME_W-1:0] length_eff;

    wire input_accepted;
    wire output_consumed;

    integer idx;
    logic [TOKEN_W-1:0] token_i;
    logic [TOKEN_W-1:0] prev_token;
    logic [COUNT_W-1:0] out_len_calc;
    logic [MAX_T*TOKEN_W-1:0] out_tokens_calc;
    logic [MAX_T*TIME_W-1:0]  out_timesteps_calc;
    logic [TIME_W-1:0] idx_time;

    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy;
    assign input_accepted  = in_valid & in_ready;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy                <= 1'b0;
            out_token_length    <= '0;
            out_timestep_length <= '0;
            out_tokens          <= '0;
            out_timesteps       <= '0;
            length_eff          <= '0;
        end else if (input_accepted) begin
            if (in_length > TIME_W'(MAX_T))
                length_eff = TIME_W'(MAX_T);
            else
                length_eff = in_length;

            out_tokens_calc     = '0;
            out_timesteps_calc  = '0;
            out_len_calc        = '0;
            prev_token          = '0;

            for (idx = 0; idx < MAX_T; idx = idx + 1) begin
                token_i  = in_tokens[idx*TOKEN_W +: TOKEN_W];
                idx_time = TIME_W'(idx);

                if ((idx_time < length_eff) &&
                    (token_i != in_blank_token) &&
                    ((idx == 0) || (token_i != prev_token))) begin
                    out_tokens_calc[out_len_calc*TOKEN_W +: TOKEN_W] = token_i;
                    out_timesteps_calc[out_len_calc*TIME_W +: TIME_W] = idx_time;
                    out_len_calc = out_len_calc + COUNT_W'(1);
                end

                prev_token = token_i;
            end

            out_token_length    <= TIME_W'(out_len_calc);
            out_timestep_length <= TIME_W'(out_len_calc);
            out_tokens          <= out_tokens_calc;
            out_timesteps       <= out_timesteps_calc;
            busy                <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
