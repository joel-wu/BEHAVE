module control_imu_estimation_mahony_ahrs_update (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic signed [15:0] in_gx,
    input logic signed [15:0] in_gy,
    input logic signed [15:0] in_gz,
    input logic signed [15:0] in_ax,
    input logic signed [15:0] in_ay,
    input logic signed [15:0] in_az,
    input logic signed [15:0] in_dt,
    input logic signed [15:0] in_kp,
    input logic signed [15:0] in_ki,
    input logic signed [15:0] in_acc_confidence_decay,
    output logic out_valid,
    input logic out_ready,
    output logic signed [15:0] out_q0,
    output logic signed [15:0] out_q1,
    output logic signed [15:0] out_q2,
    output logic signed [15:0] out_q3,
    output logic signed [31:0] out_integralFBx,
    output logic signed [31:0] out_integralFBy,
    output logic signed [31:0] out_integralFBz,
    output logic [15:0] out_accMagP
);
// Wide temporaries preserve the gradient and doubled gains. No early Q15 clamp.
function automatic longint signed mul(input longint signed a, b);
    mul = (a*b) >>> 15;
endfunction

function automatic longint signed clip(input longint signed v, lo, hi);
    clip = v < lo ? lo : (v > hi ? hi : v);
endfunction

function automatic longint signed root96(input logic [95:0] n);
    logic [47:0] root;
    logic [49:0] remainder, trial;
    root = 0;
    remainder = 0;
    for (int k=0; k<48; k=k+1) begin
        remainder = (remainder << 2) | {48'b0,n[94-2*k +: 2]};
        trial = ({2'b0,root} << 2) | 50'd1;
        root = root << 1;
        if (remainder >= trial) begin
            remainder = remainder - trial;
            root = root | 48'd1;
        end
    end
    root96 = $signed({16'b0,root});
endfunction

function automatic longint signed norm_den(input longint signed a,b,c,d);
    logic [95:0] sum;
    sum = 96'(a*a + b*b + c*c + d*d);
    norm_den = root96(sum << 32);
endfunction

function automatic longint signed unit(input longint signed v, denominator);
    if (denominator == 0) unit = 0;
    else unit = clip((v <<< 31) / denominator, -32768, 32767);
endfunction

logic pending;
assign in_ready = !rst && (!pending || out_ready);
assign out_valid = !rst && pending;
logic signed [15:0] state_q0;
logic signed [15:0] state_q1;
logic signed [15:0] state_q2;
logic signed [15:0] state_q3;
logic [15:0] state_accMagP;
logic signed [31:0] state_integralFBx;
logic signed [31:0] state_integralFBy;
logic signed [31:0] state_integralFBz;

always_ff @(posedge clk) begin
    longint signed a;
    longint signed acc2;
    longint signed accMagP;
    longint signed acc_confidence_decay;
    longint signed ax;
    longint signed ay;
    longint signed az;
    longint signed b;
    longint signed c;
    longint signed confidence;
    longint signed delta;
    longint signed den;
    longint signed dt;
    longint signed ex;
    longint signed ey;
    longint signed ez;
    longint signed gx;
    longint signed gy;
    longint signed gz;
    longint signed hx;
    longint signed hy;
    longint signed hz;
    longint signed ik;
    longint signed integralFBx;
    longint signed integralFBy;
    longint signed integralFBz;
    longint signed ki;
    longint signed kp;
    longint signed n0;
    longint signed n1;
    longint signed n2;
    longint signed n3;
    longint signed pk;
    longint signed q0;
    longint signed q1;
    longint signed q2;
    longint signed q3;
    longint signed vx;
    longint signed vy;
    longint signed vz;
    if (rst) begin
        pending <= 0;
        state_q0 <= 16'd32767;
        state_q1 <= 16'd0;
        state_q2 <= 16'd0;
        state_q3 <= 16'd0;
        state_accMagP <= 16'd32768;
        state_integralFBx <= 32'd0;
        state_integralFBy <= 32'd0;
        state_integralFBz <= 32'd0;
        out_q0 <= 16'd32767;
        out_q1 <= 16'd0;
        out_q2 <= 16'd0;
        out_q3 <= 16'd0;
        out_integralFBx <= 32'd0;
        out_integralFBy <= 32'd0;
        out_integralFBz <= 32'd0;
        out_accMagP <= 16'd32768;
    end else if (in_valid && in_ready) begin
        q0 = state_q0;
        q1 = state_q1;
        q2 = state_q2;
        q3 = state_q3;
        accMagP = state_accMagP;
        integralFBx = state_integralFBx;
        integralFBy = state_integralFBy;
        integralFBz = state_integralFBz;
        gx = $signed(in_gx);
        gy = $signed(in_gy);
        gz = $signed(in_gz);
        ax = $signed(in_ax);
        ay = $signed(in_ay);
        az = $signed(in_az);
        dt = $signed(in_dt);
        kp = $signed(in_kp);
        ki = $signed(in_ki);
        acc_confidence_decay = $signed(in_acc_confidence_decay);
        acc2 = (((ax * ax) + (ay * ay)) + (az * az));
        if (acc2 > 64'sd107374) begin
            accMagP = (((64'sd9 * accMagP) + root96(acc2)) / 64'sd10);
            delta = (accMagP - 64'sd32768);
            delta = ((delta < 64'sd0) ? (-delta) : delta);
            confidence = clip((64'sd32768 - mul(acc_confidence_decay, root96((delta <<< 64'sd15)))), 64'sd0, 64'sd32768);
            den = norm_den(ax, ay, az, 64'sd0);
            a = unit(ax, den);
            b = unit(ay, den);
            c = unit(az, den);
            vx = (mul(q1, q3) - mul(q0, q2));
            vy = (mul(q0, q1) + mul(q2, q3));
            vz = ((mul(q0, q0) - 64'sd16384) + mul(q3, q3));
            ex = (mul(b, vz) - mul(c, vy));
            ey = (mul(c, vx) - mul(a, vz));
            ez = (mul(a, vy) - mul(b, vx));
            pk = mul((64'sd2 * kp), confidence);
            ik = mul((64'sd2 * ki), confidence);
            if (ik > 64'sd0) begin
                integralFBx = clip((integralFBx + (((ik * ex) * dt) >>> 64'sd14)), (-64'sd2147483648), 64'sd2147483647);
                integralFBy = clip((integralFBy + (((ik * ey) * dt) >>> 64'sd14)), (-64'sd2147483648), 64'sd2147483647);
                integralFBz = clip((integralFBz + (((ik * ez) * dt) >>> 64'sd14)), (-64'sd2147483648), 64'sd2147483647);
            end else begin
                integralFBx = 64'sd0;
                integralFBy = 64'sd0;
                integralFBz = 64'sd0;
            end
            gx = ((gx + (integralFBx >>> 64'sd16)) + mul(pk, ex));
            gy = ((gy + (integralFBy >>> 64'sd16)) + mul(pk, ey));
            gz = ((gz + (integralFBz >>> 64'sd16)) + mul(pk, ez));
        end
        hx = ((gx * dt) >>> 64'sd16);
        hy = ((gy * dt) >>> 64'sd16);
        hz = ((gz * dt) >>> 64'sd16);
        n0 = (((q0 - mul(q1, hx)) - mul(q2, hy)) - mul(q3, hz));
        n1 = (((q1 + mul(q0, hx)) + mul(q2, hz)) - mul(q3, hy));
        n2 = (((q2 + mul(q0, hy)) - mul(q1, hz)) + mul(q3, hx));
        n3 = (((q3 + mul(q0, hz)) + mul(q1, hy)) - mul(q2, hx));
        den = norm_den(n0, n1, n2, n3);
        q0 = ((den != 64'sd0) ? unit(n0, den) : 64'sd32767);
        q1 = unit(n1, den);
        q2 = unit(n2, den);
        q3 = unit(n3, den);
        state_q0 <= q0[15:0];
        state_q1 <= q1[15:0];
        state_q2 <= q2[15:0];
        state_q3 <= q3[15:0];
        state_accMagP <= accMagP[15:0];
        state_integralFBx <= integralFBx[31:0];
        state_integralFBy <= integralFBy[31:0];
        state_integralFBz <= integralFBz[31:0];
        out_q0 <= q0[15:0];
        out_q1 <= q1[15:0];
        out_q2 <= q2[15:0];
        out_q3 <= q3[15:0];
        out_integralFBx <= integralFBx[31:0];
        out_integralFBy <= integralFBy[31:0];
        out_integralFBz <= integralFBz[31:0];
        out_accMagP <= accMagP[15:0];
        pending <= 1;
    end else if (out_valid && out_ready) begin
        pending <= 0;
    end
end
endmodule
