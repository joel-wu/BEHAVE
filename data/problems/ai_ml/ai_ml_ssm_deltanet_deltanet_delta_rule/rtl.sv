module DeltaNetDeltaRule #(
    parameter int K = 4,
    parameter int V = 4,
    parameter int W = 32
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic                         in_init,
    input  logic                         in_clear_state,
    input  logic [W-1:0]                 in_scale,
    input  logic [W-1:0]                 in_beta_scalar,
    input  logic                         in_beta_headwise,
    input  logic [K*W-1:0]               in_q,
    input  logic [K*W-1:0]               in_k,
    input  logic [V*W-1:0]               in_v,
    input  logic [V*W-1:0]               in_beta_vector,
    input  logic [K*V*W-1:0]             in_initial_state,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [V*W-1:0]               out_o,
    output logic [V*W-1:0]               out_u,
    output logic [K*V*W-1:0]             out_final_state
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    logic [W-1:0] h_state [0:K-1][0:V-1];

    integer i, j;
    logic signed [W-1:0] q_val;
    logic signed [W-1:0] k_val;
    logic signed [W-1:0] v_val;
    logic signed [W-1:0] beta_vec_val;
    logic signed [W-1:0] init_val;
    logic signed [W-1:0] scale_val;
    logic signed [W-1:0] beta_scalar_val;
    logic signed [W-1:0] beta_val;
    logic signed [W-1:0] old_state_val;
    logic signed [W-1:0] projection_term;
    logic signed [W-1:0] projection_sum;
    logic signed [W-1:0] u_val;
    logic signed [W-1:0] delta_v_val;
    logic signed [W-1:0] update_term;
    logic signed [W-1:0] new_state_val;
    logic signed [W-1:0] scaled_q_val;
    logic signed [W-1:0] output_term;
    logic signed [W-1:0] output_sum;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy <= 1'b0;
            out_o <= '0;
            out_u <= '0;
            out_final_state <= '0;
            for (i = 0; i < K; i = i + 1) begin
                for (j = 0; j < V; j = j + 1) begin
                    h_state[i][j] <= '0;
                end
            end
        end else if (input_accepted) begin
            scale_val = $signed(in_scale);
            beta_scalar_val = $signed(in_beta_scalar);

            out_o <= '0;
            out_u <= '0;
            out_final_state <= '0;

            for (j = 0; j < V; j = j + 1) begin
                v_val = $signed(in_v[(j*W) +: W]);
                beta_vec_val = $signed(in_beta_vector[(j*W) +: W]);

                projection_sum = '0;
                for (i = 0; i < K; i = i + 1) begin
                    k_val = $signed(in_k[(i*W) +: W]);
                    init_val = $signed(in_initial_state[(((i*V)+j)*W) +: W]);

                    if (in_clear_state) begin
                        old_state_val = '0;
                    end else if (in_init) begin
                        old_state_val = init_val;
                    end else begin
                        old_state_val = h_state[i][j];
                    end

                    projection_term = old_state_val * k_val;
                    projection_sum = projection_sum + projection_term;
                end

                u_val = v_val - projection_sum;
                out_u[(j*W) +: W] <= u_val;

                if (in_beta_headwise) begin
                    beta_val = beta_vec_val;
                end else begin
                    beta_val = beta_scalar_val;
                end

                delta_v_val = u_val * beta_val;

                output_sum = '0;
                for (i = 0; i < K; i = i + 1) begin
                    q_val = $signed(in_q[(i*W) +: W]);
                    k_val = $signed(in_k[(i*W) +: W]);
                    init_val = $signed(in_initial_state[(((i*V)+j)*W) +: W]);

                    if (in_clear_state) begin
                        old_state_val = '0;
                    end else if (in_init) begin
                        old_state_val = init_val;
                    end else begin
                        old_state_val = h_state[i][j];
                    end

                    update_term = k_val * delta_v_val;
                    new_state_val = old_state_val + update_term;
                    h_state[i][j] <= new_state_val;
                    out_final_state[(((i*V)+j)*W) +: W] <= new_state_val;

                    scaled_q_val = q_val * scale_val;
                    output_term = new_state_val * scaled_q_val;
                    output_sum = output_sum + output_term;
                end

                out_o[(j*W) +: W] <= output_sum;
            end

            busy <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule